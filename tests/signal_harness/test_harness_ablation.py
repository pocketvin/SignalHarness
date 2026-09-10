from pathlib import Path

import pytest

from signal_harness.agent_integration.harness import HarnessVariant
from signal_harness.harness_eval import run_harness_ablation


@pytest.mark.asyncio
async def test_harness_ablation_uses_frozen_inputs_and_removes_supervisor_call(
    project_root: Path,
) -> None:
    summary = await run_harness_ablation(
        root=project_root,
        config_dir=project_root / "configs",
        fixture=project_root / "examples/signal_harness/regression_events.json",
        expectations=project_root / "examples/signal_harness/regression_expectations.json",
    )

    assert summary.passed is True
    assert summary.inputs_frozen is True
    assert len({item.input_fingerprint for item in summary.variants}) == 1
    baseline, routed, deferred, evidence, deterministic_merged, selective, merged, verified = summary.variants
    assert baseline.variant is HarnessVariant.FIVE_AGENT
    assert routed.variant is HarnessVariant.DETERMINISTIC_SUPERVISOR
    assert deferred.variant is HarnessVariant.DETERMINISTIC_SUPERVISOR_DEFERRED_LEARNING
    assert evidence.variant is HarnessVariant.DETERMINISTIC_EVIDENCE_RESOLVER
    assert deterministic_merged.variant is HarnessVariant.DETERMINISTIC_EVIDENCE_IMPACT_ACTION
    assert selective.variant is HarnessVariant.SELECTIVE_EVIDENCE_RESEARCHER
    assert merged.variant is HarnessVariant.SELECTIVE_EVIDENCE_IMPACT_ACTION
    assert verified.variant is HarnessVariant.SELECTIVE_EVIDENCE_IMPACT_ACTION_VERIFIER
    assert routed.llm_call_count < baseline.llm_call_count
    assert deferred.llm_call_count < routed.llm_call_count
    assert evidence.llm_call_count < deferred.llm_call_count
    assert deterministic_merged.llm_call_count < evidence.llm_call_count
    assert selective.llm_call_count < deferred.llm_call_count
    assert "SignalSupervisorAgent" in baseline.llm_calls_by_agent
    assert "SignalSupervisorAgent" not in routed.llm_calls_by_agent
    assert "LearningPolicyAgent" not in deferred.llm_calls_by_agent
    assert "ContextEvidenceAgent" not in evidence.llm_calls_by_agent
    assert "ContextEvidenceAgent" not in deterministic_merged.llm_calls_by_agent
    assert deterministic_merged.llm_calls_by_agent.get("ImpactActionAnalyzerAgent") == 1
    assert "ImpactAnalystAgent" not in deterministic_merged.llm_calls_by_agent
    assert "ActionPlannerAgent" not in deterministic_merged.llm_calls_by_agent
    assert selective.llm_calls_by_agent.get("ContextEvidenceAgent") == 1
    assert merged.llm_calls_by_agent.get("ContextEvidenceAgent") == 1
    assert merged.llm_calls_by_agent.get("ImpactActionAnalyzerAgent") == 1
    assert "ActionPlannerAgent" not in merged.llm_calls_by_agent
    assert merged.llm_call_count < selective.llm_call_count
    assert verified.llm_calls_by_agent.get("SelectiveVerifierAgent") == 1
    assert verified.llm_call_count > merged.llm_call_count
    assert evidence.regression.decision_accuracy >= baseline.regression.decision_accuracy
    assert deterministic_merged.regression.decision_accuracy >= baseline.regression.decision_accuracy
    assert selective.regression.decision_accuracy >= baseline.regression.decision_accuracy
    assert merged.regression.decision_accuracy >= baseline.regression.decision_accuracy
    assert verified.regression.decision_accuracy >= baseline.regression.decision_accuracy
    assert summary.recommendation is HarnessVariant.DETERMINISTIC_EVIDENCE_IMPACT_ACTION


def test_selection_prefers_no_optional_evidence_research_when_quality_and_calls_tie() -> None:
    from signal_harness.harness_eval import HarnessVariantEval, _selection_key
    from signal_harness.evals import RegressionEvalSummary, RegressionThresholds

    regression = RegressionEvalSummary(
        suite="tie",
        case_count=1,
        assessed_count=1,
        decision_accuracy=1.0,
        category_accuracy=1.0,
        priority_precision=1.0,
        priority_recall=1.0,
        false_positive_rate=0.0,
        false_negative_rate=0.0,
        true_positive=1,
        false_positive=0,
        true_negative=0,
        false_negative=0,
        thresholds=RegressionThresholds(),
        passed=True,
    )
    common = dict(
        input_fingerprint="frozen",
        event_count=1,
        analyzer_version="v1",
        prompt_version="p1",
        context_packet_version="c1",
        policy_version="1",
        provider="mock",
        model="mock",
        model_profile="mock",
        regression=regression,
        llm_call_count=2,
        llm_latency_ms=1,
        total_tokens=0,
        estimated_cost_usd=0.0,
        fallback_rate=0.0,
        wall_clock_ms=1,
    )
    deterministic = HarnessVariantEval(
        variant=HarnessVariant.DETERMINISTIC_EVIDENCE_RESOLVER,
        llm_calls_by_agent={"ImpactAnalystAgent": 1, "ActionPlannerAgent": 1},
        **common,
    )
    selective = HarnessVariantEval(
        variant=HarnessVariant.SELECTIVE_EVIDENCE_IMPACT_ACTION,
        llm_calls_by_agent={"ContextEvidenceAgent": 1, "ImpactActionAnalyzerAgent": 1},
        **common,
    )

    assert _selection_key(deterministic) < _selection_key(selective)


def test_non_inferior_metrics_require_every_dimension_to_hold() -> None:
    from signal_harness.harness_eval import _metrics_non_inferior

    baseline = (0.90, 0.90, 0.90, 0.90)
    assert _metrics_non_inferior((0.91, 0.90, 0.90, 0.90), baseline) is True
    assert _metrics_non_inferior((0.95, 0.90, 0.89, 0.90), baseline) is False
    assert _metrics_non_inferior((0.90, 0.91, 0.90, 0.89), baseline) is False


@pytest.mark.asyncio
async def test_real_world_harness_corpus_preserves_project_state_hard_negatives(
    project_root: Path,
) -> None:
    summary = await run_harness_ablation(
        root=project_root,
        config_dir=project_root / "configs",
        fixture=project_root / "examples/signal_harness/real_world_events_2026q3.json",
        expectations=project_root / "examples/signal_harness/real_world_expectations_2026q3.json",
    )

    assert summary.passed is True
    assert summary.inputs_frozen is True
    for item in summary.variants:
        assert item.regression.decision_accuracy == 1.0
        assert item.regression.priority_precision == 1.0
        assert item.regression.priority_recall == 1.0
        assert item.regression.missing_event_ids == []
