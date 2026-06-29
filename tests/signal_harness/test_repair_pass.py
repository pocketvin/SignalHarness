from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from signal_harness.agent_integration.mode import RunMode
from signal_harness.agent_integration.runner import AgentLoopLimits
from signal_harness.agent_integration.schemas import (
    ActionItem,
    ActionOutput,
    ContextEvidenceItem,
    ContextEvidenceOutput,
    ImpactItem,
    ImpactOutput,
    RepairRequest,
)
from signal_harness.providers.adapter import AgentCall
from signal_harness.providers.mock_provider import MockProvider
from signal_harness.runtime.workflow import SignalHarnessWorkflow
from signal_harness.signal.schemas import SourceQuality
from signal_harness.ui.trace_view import write_trace_summary

IMPACT_IDS = ["demo-001", "demo-002", "demo-003", "demo-004"]
ACTION_IDS = ["demo-001", "demo-002", "demo-004"]


class SequenceProvider(MockProvider):
    def __init__(self, sequences: dict[str, list[str]]) -> None:
        super().__init__(strategy="scripted")
        self.sequences = {key: list(value) for key, value in sequences.items()}

    async def complete(self, call: AgentCall) -> str:
        sequence = self.sequences.get(call.output_schema)
        if sequence:
            self.calls.append(call)
            return sequence.pop(0)
        return await super().complete(call)


def _scan(
    project_root: Path,
    tmp_path: Path,
    provider: MockProvider,
    *,
    limits: AgentLoopLimits | None = None,
):
    workflow = SignalHarnessWorkflow(
        cwd=project_root,
        output_dir=tmp_path / "outputs",
        state_dir=tmp_path / "state",
        mode=RunMode.MOCK_AGENT,
        provider=provider,
        agent_loop_limits=limits,
    )
    return asyncio.run(
        workflow.scan(
            fixture=project_root / "examples/signal_harness/sample_events.json"
        )
    )


def _impact_output(
    *,
    ids: list[str] = IMPACT_IDS,
    semantic: float = 82,
    risk: str = "high",
    repairs: list[RepairRequest] | None = None,
) -> ImpactOutput:
    return ImpactOutput(
        results=[
            ImpactItem(
                event_id=event_id,
                affected_modules=["project-wide"],
                semantic_relevance=semantic,
                risk_level=risk,  # type: ignore[arg-type]
                impact_reason=f"impact for {event_id}",
            )
            for event_id in ids
        ],
        repair_requests=repairs or [],
    )


def _action_output(
    *,
    ids: list[str] = ACTION_IDS,
    requested_actions: list[str] | None = None,
    approval_required: bool = False,
    repairs: list[RepairRequest] | None = None,
) -> ActionOutput:
    return ActionOutput(
        results=[
            ActionItem(
                event_id=event_id,
                action_items=["Review the signal."],
                critic_notes=f"action for {event_id}",
                approval_required=approval_required,
                requested_actions=requested_actions or [],
            )
            for event_id in ids
        ],
        repair_requests=repairs or [],
    )


def _low_evidence(ids: list[str] = IMPACT_IDS) -> ContextEvidenceOutput:
    return ContextEvidenceOutput(
        results=[
            ContextEvidenceItem(
                event_id=event_id,
                evidence_urls=[],
                context_summary=f"low-confidence evidence for {event_id}",
                confidence=0.2,
                source_quality=SourceQuality.UNVERIFIED,
                uncertainty="needs repair",
            )
            for event_id in ids
        ]
    )


def _repair_details(result: Any, step: str) -> list[str]:
    return [item.detail for item in result.trace.steps if item.step == step]


def _repair_metadata(result: Any, step: str) -> list[dict[str, Any]]:
    payloads: list[dict[str, Any]] = []
    for item in result.trace.steps:
        if item.step != step:
            continue
        repair = item.metadata.get("repair")
        if isinstance(repair, dict):
            payloads.append(repair)
    return payloads


def test_explicit_impact_to_evidence_repair_executes(
    project_root: Path,
    tmp_path: Path,
) -> None:
    request = RepairRequest(
        target_agent="context_evidence",
        event_ids=["demo-001"],
        reason="High-risk impact needs stronger evidence.",
        severity="high",
    )
    provider = SequenceProvider(
        {
            "ImpactOutput": [
                _impact_output(repairs=[request]).model_dump_json(),
                _impact_output(ids=["demo-001"], semantic=88).model_dump_json(),
            ]
        }
    )

    result = _scan(project_root, tmp_path, provider)

    assert _repair_details(result, "repair_requested")
    assert _repair_details(result, "repair_context_evidence")
    assert _repair_details(result, "repair_impact")
    requested_metadata = _repair_metadata(result, "repair_requested")[0]
    assert requested_metadata["triggered_by"] == "ImpactAnalystAgent"
    assert requested_metadata["target_agent"] == "context_evidence"
    assert requested_metadata["event_ids"] == ["demo-001"]
    assert requested_metadata["repair_round"] == 1
    assert sum(call.output_schema == "ImpactOutput" for call in provider.calls) == 2
    repair_llm_steps = [
        step
        for step in result.trace.steps
        if step.step == "llm_agent_call"
        and "repair_internal_llm_call=true" in step.detail
    ]
    assert repair_llm_steps
    assert all("summary_step=repair_" in step.detail for step in repair_llm_steps)
    assert all(
        step.metadata.get("repair", {}).get("internal_llm_call") is True
        for step in repair_llm_steps
    )
    summary = write_trace_summary(
        tmp_path / "trace-summary",
        result.trace.steps,
    ).read_text(encoding="utf-8")
    assert "## Agent Repair Pass" in summary
    assert "## Repair Internal LLM Calls" in summary
    assert "separate from the repair summary steps above" in summary


def test_deterministic_impact_to_evidence_repair_trigger(
    project_root: Path,
    tmp_path: Path,
) -> None:
    provider = SequenceProvider(
        {
            "ContextEvidenceOutput": [
                _low_evidence().model_dump_json(),
            ],
            "ImpactOutput": [
                _impact_output(semantic=90, risk="high").model_dump_json(),
                _impact_output(ids=IMPACT_IDS, semantic=90, risk="high").model_dump_json(),
            ],
        }
    )

    result = _scan(project_root, tmp_path, provider)

    requested = "\n".join(_repair_details(result, "repair_requested"))
    assert "deterministic trigger" in requested
    assert _repair_details(result, "repair_context_evidence")


def test_impact_repair_to_invalid_target_is_blocked(
    project_root: Path,
    tmp_path: Path,
) -> None:
    request = RepairRequest(
        target_agent="impact",
        event_ids=["demo-001"],
        reason="Impact cannot repair itself.",
    )
    provider = SequenceProvider(
        {"ImpactOutput": [_impact_output(repairs=[request]).model_dump_json()]}
    )

    result = _scan(project_root, tmp_path, provider)

    assert "may only request context_evidence repair" in "\n".join(
        _repair_details(result, "repair_blocked")
    )
    assert not _repair_details(result, "repair_context_evidence")


def test_repair_round_budget_blocks_execution(
    project_root: Path,
    tmp_path: Path,
) -> None:
    request = RepairRequest(
        target_agent="context_evidence",
        event_ids=["demo-001"],
        reason="Budget should block this repair.",
    )
    provider = SequenceProvider(
        {"ImpactOutput": [_impact_output(repairs=[request]).model_dump_json()]}
    )

    result = _scan(
        project_root,
        tmp_path,
        provider,
        limits=AgentLoopLimits(max_repair_rounds_per_run=0),
    )

    assert _repair_details(result, "repair_requested")
    assert "repair_round_budget_exceeded" in "\n".join(
        _repair_details(result, "repair_blocked")
    )


def test_repair_event_cap_truncates_events(
    project_root: Path,
    tmp_path: Path,
) -> None:
    request = RepairRequest(
        target_agent="context_evidence",
        event_ids=["demo-001", "demo-002"],
        reason="Only one event may be repaired.",
    )
    provider = SequenceProvider(
        {
            "ImpactOutput": [
                _impact_output(repairs=[request]).model_dump_json(),
                _impact_output(ids=["demo-001"]).model_dump_json(),
            ]
        }
    )

    result = _scan(
        project_root,
        tmp_path,
        provider,
        limits=AgentLoopLimits(max_repair_events_per_run=1),
    )

    assert "repair_event_cap_truncated" in "\n".join(
        _repair_details(result, "repair_blocked")
    )
    context_steps = [
        step for step in result.trace.steps if step.step == "repair_context_evidence"
    ]
    assert context_steps[0].input_count == 1
    assert "demo-001" in context_steps[0].detail
    assert "demo-002" not in context_steps[0].detail


def test_repair_uses_shared_tool_budget(
    project_root: Path,
    tmp_path: Path,
) -> None:
    request = RepairRequest(
        target_agent="context_evidence",
        event_ids=["demo-001"],
        reason="Tool budget remains shared with repair.",
    )
    provider = SequenceProvider(
        {
            "ImpactOutput": [
                _impact_output(repairs=[request]).model_dump_json(),
                _impact_output(ids=["demo-001"]).model_dump_json(),
            ]
        }
    )

    result = _scan(
        project_root,
        tmp_path,
        provider,
        limits=AgentLoopLimits(max_total_tool_requests_per_run=0),
    )

    repair_step = next(
        step for step in result.trace.steps if step.step == "repair_context_evidence"
    )
    assert repair_step.budget_blocked_count
    assert repair_step.tools_executed_count == 0


def test_explicit_action_to_impact_repair_executes(
    project_root: Path,
    tmp_path: Path,
) -> None:
    request = RepairRequest(
        target_agent="impact",
        event_ids=["demo-001"],
        reason="Action wants impact review.",
    )
    provider = SequenceProvider(
        {
            "ActionOutput": [
                _action_output(repairs=[request]).model_dump_json(),
                _action_output(ids=["demo-001"]).model_dump_json(),
            ]
        }
    )

    result = _scan(project_root, tmp_path, provider)

    assert any(
        "Action→Impact" in detail
        for detail in _repair_details(result, "repair_impact")
    )
    assert _repair_details(result, "repair_action")


def test_action_deterministic_repair_trigger_and_no_recursive_evidence(
    project_root: Path,
    tmp_path: Path,
) -> None:
    provider = SequenceProvider(
        {
            "ImpactOutput": [
                _impact_output(semantic=35, risk="low").model_dump_json(),
                _impact_output(
                    ids=["demo-001"],
                    semantic=65,
                    risk="medium",
                ).model_dump_json(),
            ],
            "ActionOutput": [
                _action_output(requested_actions=["create_github_issue"]).model_dump_json(),
                _action_output(ids=["demo-001"]).model_dump_json(),
            ],
        }
    )

    result = _scan(
        project_root,
        tmp_path,
        provider,
        limits=AgentLoopLimits(max_repair_rounds_per_run=2),
    )

    assert "requested actions with low semantic relevance" in "\n".join(
        _repair_details(result, "repair_requested")
    )
    assert _repair_details(result, "repair_action")
    assert not _repair_details(result, "repair_context_evidence")


def test_action_repair_is_blocked_after_impact_repair_uses_single_round(
    project_root: Path,
    tmp_path: Path,
) -> None:
    impact_repair = RepairRequest(
        target_agent="context_evidence",
        event_ids=["demo-001"],
        reason="Impact uses the only repair round.",
    )
    action_repair = RepairRequest(
        target_agent="impact",
        event_ids=["demo-001"],
        reason="Action repair should be blocked by round budget.",
    )
    provider = SequenceProvider(
        {
            "ImpactOutput": [
                _impact_output(repairs=[impact_repair]).model_dump_json(),
                _impact_output(ids=["demo-001"]).model_dump_json(),
            ],
            "ActionOutput": [
                _action_output(repairs=[action_repair]).model_dump_json(),
            ],
        }
    )

    result = _scan(
        project_root,
        tmp_path,
        provider,
        limits=AgentLoopLimits(max_repair_rounds_per_run=1),
    )

    blocked = "\n".join(_repair_details(result, "repair_blocked"))
    blocked_metadata = _repair_metadata(result, "repair_blocked")[0]
    assert "triggered_by=ActionPlannerAgent" in blocked
    assert "target_agent=impact" in blocked
    assert "repair_round_budget_exceeded" in blocked
    assert blocked_metadata["triggered_by"] == "ActionPlannerAgent"
    assert blocked_metadata["target_agent"] == "impact"
    assert blocked_metadata["blocked_reason"] == "repair_round_budget_exceeded"
    assert blocked_metadata["repair_round"] == 2
    assert not any(
        "Action→Impact" in detail
        for detail in _repair_details(result, "repair_impact")
    )
    assert not _repair_details(result, "repair_action")
