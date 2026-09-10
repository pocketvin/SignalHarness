"""Offline Harness ablation on frozen SignalHarness inputs."""

from __future__ import annotations

import hashlib
import json
import time
from collections import Counter
from pathlib import Path
from tempfile import TemporaryDirectory

from pydantic import BaseModel, ConfigDict, Field

from signal_harness.agent_integration.harness import HARNESS_EVAL_VERSION, HarnessVariant
from signal_harness.agent_integration.mode import RunMode
from signal_harness.evals import (
    RegressionEvalSummary,
    RegressionSuite,
    evaluate_regression_suite,
    load_regression_suite,
)
from signal_harness.runtime.workflow import ScanResult, SignalHarnessWorkflow
from signal_harness.utils.fs import atomic_write_text


class HarnessVariantEval(BaseModel):
    model_config = ConfigDict(extra="forbid")

    variant: HarnessVariant
    input_fingerprint: str
    event_count: int = Field(ge=0)
    analyzer_version: str
    prompt_version: str
    context_packet_version: str
    policy_version: str
    provider: str
    model: str
    model_profile: str
    regression: RegressionEvalSummary
    llm_call_count: int = Field(ge=0)
    llm_calls_by_agent: dict[str, int] = Field(default_factory=dict)
    llm_latency_ms: int = Field(ge=0)
    total_tokens: int = Field(ge=0)
    estimated_cost_usd: float = Field(ge=0)
    fallback_rate: float = Field(ge=0, le=1)
    wall_clock_ms: int = Field(ge=0)


class HarnessAblationSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    eval_version: str = HARNESS_EVAL_VERSION
    corpus_hash: str
    inputs_frozen: bool
    variants: list[HarnessVariantEval]
    recommendation: HarnessVariant
    recommendation_reason: str
    passed: bool


def _file_hash(*paths: Path) -> str:
    digest = hashlib.sha256()
    for path in paths:
        digest.update(path.read_bytes())
    return digest.hexdigest()


def _variant_result(
    *,
    variant: HarnessVariant,
    result: ScanResult,
    suite: RegressionSuite,
    elapsed_ms: int,
) -> HarnessVariantEval:
    snapshot = next(step for step in result.trace.steps if step.step == "harness_input_snapshot")
    metadata = snapshot.metadata
    llm_steps = [step for step in result.trace.steps if step.step == "llm_agent_call"]
    calls = Counter(str(step.agent or "unknown") for step in llm_steps)
    fallback_rate = (sum(step.fallback_used for step in llm_steps) / len(llm_steps)) if llm_steps else 0.0
    return HarnessVariantEval(
        variant=variant,
        input_fingerprint=str(metadata["input_fingerprint"]),
        event_count=len(result.signals),
        analyzer_version=str(metadata["analyzer_version"]),
        prompt_version=str(metadata["prompt_version"]),
        context_packet_version=str(metadata["context_packet_version"]),
        policy_version=str(metadata["policy_version"]),
        provider=str(metadata["provider"]),
        model=str(metadata["model"]),
        model_profile=str(metadata["model_profile"]),
        regression=evaluate_regression_suite(assessments=result.assessments, suite=suite),
        llm_call_count=len(llm_steps),
        llm_calls_by_agent=dict(sorted(calls.items())),
        llm_latency_ms=sum(step.duration_ms for step in llm_steps),
        total_tokens=sum(step.total_tokens or 0 for step in llm_steps),
        estimated_cost_usd=round(sum(step.estimated_cost_usd or 0.0 for step in llm_steps), 8),
        fallback_rate=round(fallback_rate, 4),
        wall_clock_ms=elapsed_ms,
    )


def _quality_tuple(item: HarnessVariantEval) -> tuple[float, float, float, float]:
    r = item.regression
    return (r.decision_accuracy, r.category_accuracy, r.priority_precision, r.priority_recall)


def _metrics_non_inferior(
    candidate: tuple[float, float, float, float],
    baseline: tuple[float, float, float, float],
) -> bool:
    """Require every guarded quality metric to match or beat the baseline."""

    return all(current >= reference for current, reference in zip(candidate, baseline, strict=True))


def _selection_key(item: HarnessVariantEval) -> tuple[int, int, int, int, str]:
    """Prefer fewer calls, then fewer optional research/verification components."""

    verifier_calls = item.llm_calls_by_agent.get("SelectiveVerifierAgent", 0)
    evidence_research_calls = item.llm_calls_by_agent.get("ContextEvidenceAgent", 0)
    distinct_agents = len(item.llm_calls_by_agent)
    return (
        item.llm_call_count,
        verifier_calls,
        evidence_research_calls,
        distinct_agents,
        item.variant.value,
    )


async def run_harness_ablation(
    *,
    root: Path,
    config_dir: Path,
    fixture: Path,
    expectations: Path,
) -> HarnessAblationSummary:
    """Compare the baseline and first simplification on identical isolated inputs."""

    suite = load_regression_suite(expectations)
    variants = [
        HarnessVariant.FIVE_AGENT,
        HarnessVariant.DETERMINISTIC_SUPERVISOR,
        HarnessVariant.DETERMINISTIC_SUPERVISOR_DEFERRED_LEARNING,
        HarnessVariant.DETERMINISTIC_EVIDENCE_RESOLVER,
        HarnessVariant.DETERMINISTIC_EVIDENCE_IMPACT_ACTION,
        HarnessVariant.SELECTIVE_EVIDENCE_RESEARCHER,
        HarnessVariant.SELECTIVE_EVIDENCE_IMPACT_ACTION,
        HarnessVariant.SELECTIVE_EVIDENCE_IMPACT_ACTION_VERIFIER,
    ]
    results: list[HarnessVariantEval] = []
    with TemporaryDirectory(prefix="signalharness-harness-eval-") as temp:
        temp_root = Path(temp)
        for variant in variants:
            start = time.perf_counter()
            workflow = SignalHarnessWorkflow(
                cwd=root,
                config_dir=config_dir,
                output_dir=temp_root / variant.value / "outputs",
                state_dir=temp_root / variant.value / "state",
                mode=RunMode.MOCK_AGENT,
                harness_variant=variant,
                offline_fixture_source_tools=True,
            )
            scan = await workflow.scan(fixture=fixture, interactive=False)
            elapsed_ms = round((time.perf_counter() - start) * 1000)
            results.append(_variant_result(variant=variant, result=scan, suite=suite, elapsed_ms=elapsed_ms))

    inputs_frozen = len({item.input_fingerprint for item in results}) == 1
    baseline = results[0]
    baseline_quality = _quality_tuple(baseline)
    eligible = [
        item
        for item in results
        if item.regression.passed
        and _metrics_non_inferior(_quality_tuple(item), baseline_quality)
    ]
    best = min(eligible or [baseline], key=_selection_key)
    recommendation = best.variant if inputs_frozen else baseline.variant
    if inputs_frozen and best.variant is not baseline.variant:
        reason = (
            f"{best.variant.value} matched or exceeded the baseline regression metrics on "
            "identical frozen inputs while minimizing LLM calls and optional "
            "research/verification components."
        )
    else:
        reason = (
            "Keep the five-Agent baseline because no simplified variant proved non-inferior "
            "quality with lower complexity on identical inputs."
        )
    passed = inputs_frozen and baseline.regression.passed and best.regression.passed
    return HarnessAblationSummary(
        corpus_hash=_file_hash(fixture, expectations),
        inputs_frozen=inputs_frozen,
        variants=results,
        recommendation=recommendation,
        recommendation_reason=reason,
        passed=passed,
    )


def write_harness_ablation_summary(output_dir: Path, summary: HarnessAblationSummary) -> dict[str, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / "harness_ablation_summary.json"
    md_path = output_dir / "harness_ablation_summary.md"
    atomic_write_text(json_path, json.dumps(summary.model_dump(mode="json"), indent=2, ensure_ascii=False) + "\n")
    lines = [
        "# SignalHarness Harness Ablation",
        "",
        f"**Result:** {'PASS' if summary.passed else 'FAIL'}",
        f"**Frozen inputs:** {str(summary.inputs_frozen).lower()}",
        f"**Corpus hash:** `{summary.corpus_hash}`",
        f"**Recommendation:** `{summary.recommendation.value}`",
        "",
        summary.recommendation_reason,
        "",
        "| Variant | Decision | Category | Precision | Recall | LLM calls | LLM latency ms | Tokens |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for item in summary.variants:
        r = item.regression
        lines.append(
            f"| {item.variant.value} | {r.decision_accuracy:.4f} | {r.category_accuracy:.4f} | "
            f"{r.priority_precision:.4f} | {r.priority_recall:.4f} | {item.llm_call_count} | "
            f"{item.llm_latency_ms} | {item.total_tokens} |"
        )
    lines.extend(["", "This is a project-specific offline ablation, not a general model benchmark.", ""] )
    atomic_write_text(md_path, "\n".join(lines))
    return {"json": json_path, "markdown": md_path}
