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
    baseline, routed, deferred, evidence, selective = summary.variants
    assert baseline.variant is HarnessVariant.FIVE_AGENT
    assert routed.variant is HarnessVariant.DETERMINISTIC_SUPERVISOR
    assert deferred.variant is HarnessVariant.DETERMINISTIC_SUPERVISOR_DEFERRED_LEARNING
    assert evidence.variant is HarnessVariant.DETERMINISTIC_EVIDENCE_RESOLVER
    assert selective.variant is HarnessVariant.SELECTIVE_EVIDENCE_RESEARCHER
    assert routed.llm_call_count < baseline.llm_call_count
    assert deferred.llm_call_count < routed.llm_call_count
    assert evidence.llm_call_count < deferred.llm_call_count
    assert selective.llm_call_count < deferred.llm_call_count
    assert "SignalSupervisorAgent" in baseline.llm_calls_by_agent
    assert "SignalSupervisorAgent" not in routed.llm_calls_by_agent
    assert "LearningPolicyAgent" not in deferred.llm_calls_by_agent
    assert "ContextEvidenceAgent" not in evidence.llm_calls_by_agent
    assert selective.llm_calls_by_agent.get("ContextEvidenceAgent") == 1
    assert evidence.regression.decision_accuracy < baseline.regression.decision_accuracy
    assert selective.regression.decision_accuracy >= baseline.regression.decision_accuracy
    assert summary.recommendation is HarnessVariant.SELECTIVE_EVIDENCE_RESEARCHER


def test_non_inferior_metrics_require_every_dimension_to_hold() -> None:
    from signal_harness.harness_eval import _metrics_non_inferior

    baseline = (0.90, 0.90, 0.90, 0.90)
    assert _metrics_non_inferior((0.91, 0.90, 0.90, 0.90), baseline) is True
    assert _metrics_non_inferior((0.95, 0.90, 0.89, 0.90), baseline) is False
    assert _metrics_non_inferior((0.90, 0.91, 0.90, 0.89), baseline) is False
