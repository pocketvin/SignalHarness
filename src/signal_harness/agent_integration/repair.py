"""Bounded repair candidate selection, caps, trace metadata, and merges."""

from __future__ import annotations

from typing import Any

from signal_harness.agent_integration.schemas import (
    ActionOutput,
    ContextEvidenceOutput,
    ImpactOutput,
)
from signal_harness.signal.schemas import SignalEvent, TraceStep
from signal_harness.runtime.tracing import TraceRecorder


class RepairCoordinator:
    """Keep bounded repair state outside the main Agent runner."""

    def __init__(
        self,
        *,
        trace: TraceRecorder,
        max_repair_rounds_per_run: int,
        max_repair_events_per_run: int,
    ) -> None:
        self.trace = trace
        self.max_repair_rounds_per_run = max_repair_rounds_per_run
        self.max_repair_events_per_run = max_repair_events_per_run
        self.repair_rounds_used = 0
        self.repair_events_used = 0

    def next_repair_round(self) -> int:
        return self.repair_rounds_used + 1

    def impact_to_evidence_repair_candidates(
        self,
        *,
        events: list[SignalEvent],
        evidence: ContextEvidenceOutput,
        impact: ImpactOutput,
    ) -> tuple[list[str], str]:
        valid_ids = {event.event_id for event in events}
        event_ids: list[str] = []
        reasons: list[str] = []
        for request in impact.repair_requests:
            if request.target_agent != "context_evidence":
                self.append_repair_blocked(
                    triggered_by="ImpactAnalystAgent",
                    target_agent=request.target_agent,
                    event_ids=request.event_ids,
                    reason="ImpactAnalystAgent may only request context_evidence repair.",
                    repair_round=self.next_repair_round(),
                )
                continue
            event_ids.extend(request.event_ids)
            reasons.append(request.reason)
        evidence_by_id = {item.event_id: item for item in evidence.results}
        for item in impact.results:
            evidence_item = evidence_by_id.get(item.event_id)
            if (
                item.event_id in valid_ids
                and evidence_item is not None
                and item.risk_level in {"high", "critical"}
                and evidence_item.confidence < 0.45
                and item.semantic_relevance >= 70
            ):
                event_ids.append(item.event_id)
                reasons.append(
                    "deterministic trigger: high/critical impact with "
                    "low evidence confidence"
                )
        return (
            list(dict.fromkeys(event_ids)),
            "; ".join(dict.fromkeys(reasons)) or "Impact requested evidence repair.",
        )

    def action_to_impact_repair_candidates(
        self,
        *,
        events: list[SignalEvent],
        impact: ImpactOutput,
        action: ActionOutput,
    ) -> tuple[list[str], str]:
        valid_ids = {event.event_id for event in events}
        event_ids: list[str] = []
        reasons: list[str] = []
        for request in action.repair_requests:
            if request.target_agent != "impact":
                self.append_repair_blocked(
                    triggered_by="ActionPlannerAgent",
                    target_agent=request.target_agent,
                    event_ids=request.event_ids,
                    reason="ActionPlannerAgent may only request impact repair.",
                    repair_round=self.next_repair_round(),
                )
                continue
            event_ids.extend(request.event_ids)
            reasons.append(request.reason)
        impact_by_id = {item.event_id: item for item in impact.results}
        for item in action.results:
            impact_item = impact_by_id.get(item.event_id)
            if impact_item is None or item.event_id not in valid_ids:
                continue
            if item.requested_actions and impact_item.semantic_relevance < 50:
                event_ids.append(item.event_id)
                reasons.append(
                    "deterministic trigger: requested actions with low semantic relevance"
                )
            if item.approval_required and impact_item.risk_level == "low":
                event_ids.append(item.event_id)
                reasons.append(
                    "deterministic trigger: approval_required with low impact risk"
                )
        return (
            list(dict.fromkeys(event_ids)),
            "; ".join(dict.fromkeys(reasons)) or "Action requested impact repair.",
        )

    def prepare_repair_event_ids(
        self,
        event_ids: list[str],
        *,
        valid_ids: set[str],
        triggered_by: str,
        target_agent: str,
        repair_round: int,
    ) -> list[str]:
        invalid = [event_id for event_id in event_ids if event_id not in valid_ids]
        if invalid:
            self.append_repair_blocked(
                triggered_by=triggered_by,
                target_agent=target_agent,
                event_ids=invalid,
                reason="repair event_ids were not in the routed event set",
                repair_round=repair_round,
            )
        remaining = max(
            0,
            self.max_repair_events_per_run - self.repair_events_used,
        )
        prepared = [
            event_id
            for event_id in dict.fromkeys(event_ids)
            if event_id in valid_ids
        ]
        if remaining <= 0:
            self.append_repair_blocked(
                triggered_by=triggered_by,
                target_agent=target_agent,
                event_ids=prepared,
                reason="repair_event_budget_exceeded",
                repair_round=repair_round,
            )
            return []
        blocked = prepared[remaining:]
        if blocked:
            self.append_repair_blocked(
                triggered_by=triggered_by,
                target_agent=target_agent,
                event_ids=blocked,
                reason="repair_event_cap_truncated",
                repair_round=repair_round,
            )
        return prepared[:remaining]

    def reserve_repair_round(
        self,
        *,
        triggered_by: str,
        target_agent: str,
        event_ids: list[str],
        repair_round: int,
    ) -> bool:
        if self.repair_rounds_used >= self.max_repair_rounds_per_run:
            self.append_repair_blocked(
                triggered_by=triggered_by,
                target_agent=target_agent,
                event_ids=event_ids,
                reason="repair_round_budget_exceeded",
                repair_round=repair_round,
            )
            return False
        self.repair_rounds_used += 1
        self.repair_events_used += len(event_ids)
        return True

    def append_repair_requested(
        self,
        *,
        triggered_by: str,
        target_agent: str,
        event_ids: list[str],
        reason: str,
        severity: str,
        repair_round: int,
        summary_step: str | None = None,
    ) -> None:
        self.trace.steps.append(
            TraceStep(
                step="repair_requested",
                status="success",
                agent="RepairCoordinator",
                input_count=len(event_ids),
                output_count=len(event_ids),
                duration_ms=0,
                detail=(
                    f"triggered_by={triggered_by}; target_agent={target_agent}; "
                    f"severity={severity}; event_ids={','.join(event_ids)}; "
                    f"reason={reason}"
                ),
                metadata=repair_metadata(
                    triggered_by=triggered_by,
                    target_agent=target_agent,
                    event_ids=event_ids,
                    reason=reason,
                    severity=severity,
                    repair_round=repair_round,
                    summary_step=summary_step,
                ),
            )
        )

    def append_repair_blocked(
        self,
        *,
        triggered_by: str,
        target_agent: str,
        event_ids: list[str],
        reason: str,
        repair_round: int,
    ) -> None:
        self.trace.steps.append(
            TraceStep(
                step="repair_blocked",
                status="success",
                agent="RepairCoordinator",
                input_count=len(event_ids),
                output_count=0,
                duration_ms=0,
                fallback_used=True,
                detail=(
                    f"triggered_by={triggered_by}; target_agent={target_agent}; "
                    f"event_ids={','.join(event_ids)}; reason={reason}"
                ),
                metadata=repair_metadata(
                    triggered_by=triggered_by,
                    target_agent=target_agent,
                    event_ids=event_ids,
                    reason=reason,
                    repair_round=repair_round,
                    blocked_reason=reason,
                ),
            )
        )

    def mark_repair_llm_trace(
        self,
        trace_index: int,
        *,
        phase: str,
        summary_step: str,
        event_ids: list[str],
        repair_round: int,
    ) -> None:
        trace = self.trace.steps[trace_index]
        self.trace.steps[trace_index] = trace.model_copy(
            update={
                "detail": (
                    "repair_internal_llm_call=true; "
                    f"repair_phase={phase}; summary_step={summary_step}"
                ),
                "metadata": repair_metadata(
                    event_ids=event_ids,
                    repair_round=repair_round,
                    summary_step=summary_step,
                    internal_llm_call=True,
                    phase=phase,
                ),
            }
        )


def repair_metadata(
    *,
    triggered_by: str | None = None,
    target_agent: str | None = None,
    event_ids: list[str] | None = None,
    reason: str | None = None,
    severity: str | None = None,
    repair_round: int | None = None,
    blocked_reason: str | None = None,
    summary_step: str | None = None,
    internal_llm_call: bool = False,
    phase: str | None = None,
) -> dict[str, Any]:
    repair: dict[str, Any] = {
        "triggered_by": triggered_by,
        "target_agent": target_agent,
        "event_ids": event_ids or [],
        "reason": reason,
        "severity": severity,
        "repair_round": repair_round,
        "blocked_reason": blocked_reason,
        "summary_step": summary_step,
    }
    if internal_llm_call:
        repair["internal_llm_call"] = True
    if phase:
        repair["phase"] = phase
    return {"repair": repair}


def merge_context_evidence(
    original: ContextEvidenceOutput,
    repaired: ContextEvidenceOutput,
) -> ContextEvidenceOutput:
    replacements = {item.event_id: item for item in repaired.results}
    merged = [
        replacements.pop(item.event_id, item)
        for item in original.results
    ]
    merged.extend(replacements.values())
    return original.model_copy(update={"results": merged})


def merge_impact(
    original: ImpactOutput,
    repaired: ImpactOutput,
) -> ImpactOutput:
    replacements = {item.event_id: item for item in repaired.results}
    merged = [
        replacements.pop(item.event_id, item)
        for item in original.results
    ]
    merged.extend(replacements.values())
    return original.model_copy(
        update={
            "results": merged,
            "repair_requests": list(original.repair_requests),
        }
    )


def merge_action(
    original: ActionOutput,
    repaired: ActionOutput,
) -> ActionOutput:
    replacements = {item.event_id: item for item in repaired.results}
    merged = [
        replacements.pop(item.event_id, item)
        for item in original.results
    ]
    merged.extend(replacements.values())
    return original.model_copy(
        update={
            "results": merged,
            "repair_requests": list(original.repair_requests),
        }
    )
