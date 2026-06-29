"""Small deterministic metrics for scripted Agent workflow evaluation."""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from signal_harness.signal.schemas import (
    SignalAssessment,
    SignalCategory,
    SignalDecision,
    TraceStep,
)
from signal_harness.utils.fs import atomic_write_text

READ_ONLY_EVAL_TOOLS = {
    "github_signal",
    "rss_signal",
    "web_change",
    "signal_memory",
    "signal_score",
}


class EvalSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    route_accuracy: float = Field(ge=0, le=1)
    noise_filter_accuracy: float = Field(ge=0, le=1)
    evidence_primary_source_coverage: float = Field(ge=0, le=1)
    disallowed_tool_block_rate: float = Field(ge=0, le=1)
    fallback_rate: float = Field(ge=0, le=1)
    proposal_safety_passed: bool


class ModelEvalSummary(BaseModel):
    """Comparable per-model Harness metrics computed from local outputs."""

    model_config = ConfigDict(extra="forbid")

    runs: int = Field(ge=1)
    provider: str
    model: str
    model_profile: str
    schema_valid_rate: float = Field(ge=0, le=1)
    retry_rate: float = Field(ge=0, le=1)
    fallback_rate: float = Field(ge=0, le=1)
    timeout_count: int = Field(ge=0)
    tool_plan_valid_rate: float = Field(ge=0, le=1)
    tool_budget_block_rate: float = Field(ge=0, le=1)
    blocked_tool_count: int = Field(ge=0)
    tool_error_count: int = Field(ge=0)
    decision_counts: dict[str, int]
    action_required_count: int = Field(ge=0)
    alert_count: int = Field(ge=0)
    average_latency_ms: float = Field(ge=0)


def build_eval_summary(
    assessments: list[SignalAssessment],
    trace: list[TraceStep],
    *,
    expected_categories: dict[str, SignalCategory],
    expected_noise_ids: set[str],
    proposal_safety_passed: bool,
) -> EvalSummary:
    by_id = {item.event_id: item for item in assessments}
    route_matches = sum(
        by_id.get(event_id) is not None
        and by_id[event_id].category is expected
        for event_id, expected in expected_categories.items()
    )
    noise_matches = sum(
        by_id.get(event_id) is not None
        and by_id[event_id].decision is SignalDecision.IGNORE
        for event_id in expected_noise_ids
    )
    primary = [
        item
        for item in assessments
        if item.source_quality.value == "official"
    ]
    covered = sum(bool(item.evidence_urls) for item in primary)
    requested_disallowed = [
        tool
        for step in trace
        for tool in step.tools_requested
        if tool not in READ_ONLY_EVAL_TOOLS
    ]
    blocked = sum(
        tool in {
            blocked_tool
            for step in trace
            for blocked_tool in step.blocked_tools
        }
        for tool in requested_disallowed
    )
    llm_steps = [step for step in trace if step.step == "llm_agent_call"]
    return EvalSummary(
        route_accuracy=(
            route_matches / len(expected_categories)
            if expected_categories
            else 1.0
        ),
        noise_filter_accuracy=(
            noise_matches / len(expected_noise_ids)
            if expected_noise_ids
            else 1.0
        ),
        evidence_primary_source_coverage=(
            covered / len(primary) if primary else 1.0
        ),
        disallowed_tool_block_rate=(
            blocked / len(requested_disallowed)
            if requested_disallowed
            else 1.0
        ),
        fallback_rate=(
            sum(step.fallback_used for step in llm_steps) / len(llm_steps)
            if llm_steps
            else 0.0
        ),
        proposal_safety_passed=proposal_safety_passed,
    )


def build_model_eval_summary(
    *,
    assessments: list[SignalAssessment],
    trace: list[TraceStep],
    runs: int,
    provider: str,
    model: str,
    model_profile: str,
) -> ModelEvalSummary:
    """Build simple model-comparison metrics from one or more local runs."""

    llm_steps = [step for step in trace if step.step == "llm_agent_call"]
    tool_plan_steps = [
        step for step in llm_steps if step.output_schema == "EvidenceToolPlan"
    ]
    schema_valid_count = sum(step.schema_valid is True for step in llm_steps)
    retry_count = sum(step.retry_count > 0 for step in llm_steps)
    fallback_count = sum(step.fallback_used for step in llm_steps)
    timeout_count = sum(
        "provider_timeout" in " ".join(
            value
            for value in (step.schema_error, step.error, step.detail)
            if value
        )
        for step in llm_steps
    )
    total_tool_requests = sum(
        step.tools_requested_count
        if step.tools_requested_count is not None
        else len(step.tools_requested)
        for step in trace
    )
    budget_blocks = sum(step.budget_blocked_count or 0 for step in trace)
    blocked_tool_count = sum(len(step.blocked_tools) for step in trace)
    tool_error_count = sum(len(step.tool_errors) for step in trace)
    decisions = Counter(item.decision.value for item in assessments)
    return ModelEvalSummary(
        runs=runs,
        provider=provider,
        model=model,
        model_profile=model_profile,
        schema_valid_rate=_rate(schema_valid_count, len(llm_steps), default=1.0),
        retry_rate=_rate(retry_count, len(llm_steps)),
        fallback_rate=_rate(fallback_count, len(llm_steps)),
        timeout_count=timeout_count,
        tool_plan_valid_rate=_rate(
            sum(step.schema_valid is True for step in tool_plan_steps),
            len(tool_plan_steps),
            default=1.0,
        ),
        tool_budget_block_rate=_rate(budget_blocks, total_tool_requests),
        blocked_tool_count=blocked_tool_count,
        tool_error_count=tool_error_count,
        decision_counts=dict(sorted(decisions.items())),
        action_required_count=decisions["action_required"],
        alert_count=decisions["alert"],
        average_latency_ms=round(
            sum(step.duration_ms for step in llm_steps) / len(llm_steps),
            2,
        )
        if llm_steps
        else 0.0,
    )


def write_model_eval_summary(
    output_dir: str | Path,
    summary: ModelEvalSummary,
) -> dict[str, Path]:
    """Write outputs/model_eval_summary.json and .md."""

    root = Path(output_dir).expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    json_path = root / "model_eval_summary.json"
    md_path = root / "model_eval_summary.md"
    atomic_write_text(
        json_path,
        json.dumps(summary.model_dump(mode="json"), indent=2, ensure_ascii=False)
        + "\n",
    )
    atomic_write_text(md_path, _render_model_eval_markdown(summary))
    return {"json": json_path, "markdown": md_path}


def _render_model_eval_markdown(summary: ModelEvalSummary) -> str:
    lines = [
        "# SignalHarness Model Eval Summary",
        "",
        f"- provider: {summary.provider}",
        f"- model: {summary.model}",
        f"- model_profile: {summary.model_profile}",
        f"- runs: {summary.runs}",
        "",
        "## Metrics",
        "",
    ]
    for key, value in summary.model_dump(mode="json").items():
        if key in {"provider", "model", "model_profile", "runs"}:
            continue
        lines.append(f"- {key}: {value}")
    return "\n".join(lines).rstrip() + "\n"


def _rate(numerator: int, denominator: int, *, default: float = 0.0) -> float:
    return round(numerator / denominator, 4) if denominator else default
