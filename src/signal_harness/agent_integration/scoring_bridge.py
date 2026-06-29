"""Bridge schema-validated Agent outputs into guarded SignalAssessment objects."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from signal_harness.agent_integration.schemas import (
    ActionOutput,
    ContextEvidenceOutput,
    ImpactOutput,
    SupervisorOutput,
)
from signal_harness.runtime.permissions import SignalPermissionGuard
from signal_harness.signal.policy import decision_for_score
from signal_harness.signal.scorer import score_signal
from signal_harness.signal.schemas import (
    AgentScoreBreakdown,
    FeedbackRecord,
    NoiseAssessment,
    SignalAssessment,
    SignalCategory,
    SignalDecision,
    SignalEvent,
)


def guarded_assessments(
    events: list[SignalEvent],
    *,
    routes: SupervisorOutput,
    evidence: ContextEvidenceOutput,
    impact: ImpactOutput,
    action: ActionOutput,
    project_profile: dict[str, Any],
    policy: dict[str, Any],
    noise_assessments: list[NoiseAssessment],
    seen_hashes: set[str] | None,
    feedback_history: Iterable[FeedbackRecord],
) -> tuple[list[SignalAssessment], list[str]]:
    """Create final assessments while Python owns final score and permissions."""

    route_by_id = {item.event_id: item for item in routes.routes}
    evidence_by_id = {item.event_id: item for item in evidence.results}
    impact_by_id = {item.event_id: item for item in impact.results}
    action_by_id = {item.event_id: item for item in action.results}
    noise_by_id = {item.event_id: item for item in noise_assessments}
    guard = SignalPermissionGuard(policy)
    permission_checks: list[str] = []
    assessments: list[SignalAssessment] = []
    feedback = list(feedback_history)
    weights = policy.get("agent_score_weights", {})
    deterministic_weight = float(weights.get("deterministic_base", 0.70))
    semantic_weight = float(weights.get("semantic_relevance", 0.20))
    evidence_weight = float(weights.get("evidence_confidence", 0.10))
    total = deterministic_weight + semantic_weight + evidence_weight
    if total <= 0:
        deterministic_weight, semantic_weight, evidence_weight, total = 0.7, 0.2, 0.1, 1
    deterministic_weight /= total
    semantic_weight /= total
    evidence_weight /= total

    for event in events:
        route = route_by_id[event.event_id]
        evidence_item = evidence_by_id[event.event_id]
        impact_item = impact_by_id[event.event_id]
        action_item = action_by_id[event.event_id]
        noise = noise_by_id.get(event.event_id)
        base = score_signal(
            event,
            project_profile,
            policy,
            seen_hashes=seen_hashes,
            feedback_history=feedback,
            category=route.category,
        )
        configured_category = float(
            policy.get("category_weights", {}).get(route.category.value, 1.0)
        )
        policy_multiplier = (
            0.10
            if route.category is SignalCategory.NOISE
            else max(0.85, min(1.0, 0.85 + 0.15 * configured_category))
        )
        if noise is not None:
            policy_multiplier *= noise.score_multiplier
        blended = (
            base.final_score * deterministic_weight
            + impact_item.semantic_relevance * semantic_weight
            + evidence_item.confidence * 100 * evidence_weight
        )
        final_score = round(max(0.0, min(100.0, blended * policy_multiplier)), 2)
        decision = decision_for_score(final_score, policy)
        if route.category is SignalCategory.NOISE or not route.analyze:
            decision = SignalDecision.IGNORE

        approval_notes: list[str] = []
        if route.category is SignalCategory.POLICY_SIGNAL:
            action_item = action_item.model_copy(update={"approval_required": True})
        if (
            "action" in route.required_agents
            and not action_item.requested_actions
        ):
            permission_checks.append(
                f"{event.event_id}:no-high-risk-actions-requested"
            )
        for requested in action_item.requested_actions:
            permission = guard.evaluate(requested)
            permission_checks.append(
                f"{event.event_id}:{requested}:"
                f"{'allowed' if permission.allowed else 'blocked'}"
            )
            if not permission.allowed:
                approval_notes.append(
                    f"Approval required before `{requested}`: {permission.reason}"
                )
        action_items = list(
            dict.fromkeys([*action_item.action_items, *approval_notes])
        )
        if action_item.approval_required and action_items:
            action_items.append("Human approval is required before execution.")
        if decision is SignalDecision.IGNORE:
            action_items = []

        agent_score = AgentScoreBreakdown(
            deterministic_base_score=base.final_score,
            semantic_relevance=impact_item.semantic_relevance,
            evidence_confidence_score=round(evidence_item.confidence * 100, 2),
            deterministic_weight=round(deterministic_weight, 4),
            semantic_weight=round(semantic_weight, 4),
            evidence_weight=round(evidence_weight, 4),
            policy_multiplier=round(max(0.0, min(1.0, policy_multiplier)), 4),
            final_score=final_score,
        )
        assessments.append(
            SignalAssessment(
                event_id=event.event_id,
                category=route.category,
                relevance_score=impact_item.semantic_relevance,
                impact_score=final_score,
                confidence=evidence_item.confidence,
                affected_modules=impact_item.affected_modules,
                evidence_urls=evidence_item.evidence_urls,
                source_quality=evidence_item.source_quality,
                reason=" ".join(
                    filter(
                        None,
                        [
                            route.routing_reason,
                            route.noise_reason,
                            evidence_item.context_summary,
                            evidence_item.uncertainty,
                            impact_item.impact_reason,
                            action_item.critic_notes,
                        ],
                    )
                ),
                action_items=action_items,
                decision=decision,
                score_breakdown=base,
                agent_score_breakdown=agent_score,
                related_event_ids=impact_item.related_event_ids,
                cross_source_confidence=impact_item.cross_source_confidence,
                conflicting_evidence=impact_item.conflicting_evidence,
                related_cluster_id=route.related_cluster_id,
                noise_reason=route.noise_reason or (noise.noise_reason if noise else None),
                required_agents=list(route.required_agents),
            )
        )
    return assessments, permission_checks
