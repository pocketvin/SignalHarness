"""Small deterministic metrics for scripted Agent workflow evaluation."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from signal_harness.signal.schemas import (
    SignalAssessment,
    SignalCategory,
    SignalDecision,
    TraceStep,
)

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
