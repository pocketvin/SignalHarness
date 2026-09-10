"""Bridge schema-validated Agent outputs into guarded SignalAssessment objects."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from signal_harness.agent_integration.schemas import (
    ActionOutput,
    ContextEvidenceOutput,
    ImpactOutput,
    SupervisorOutput,
)
from signal_harness.runtime.permissions import SignalPermissionGuard
from signal_harness.signal.policy import decision_for_score
from signal_harness.signal.project_state import (
    project_preference_importance,
    resolve_project_change_state,
)
from signal_harness.signal.scorer import score_signal
from signal_harness.signal.text_semantics import any_affirmed_term
from signal_harness.signal.schemas import (
    AgentScoreBreakdown,
    FeedbackRecord,
    NoiseAssessment,
    ScoreBreakdown,
    SignalAssessment,
    SignalCategory,
    SignalDecision,
    SignalEvent,
    SourceQuality,
)

GUARDED_SCORING_VERSION = "guarded-scoring-v2"


@dataclass(frozen=True)
class GuardedScoreDecision:
    """Pure deterministic result over one frozen semantic assessment."""

    final_score: float
    decision: SignalDecision
    score_breakdown: ScoreBreakdown
    agent_score_breakdown: AgentScoreBreakdown


def _agent_score_weights(policy: dict[str, Any]) -> tuple[float, float, float]:
    weights = policy.get("agent_score_weights", {})
    deterministic = float(weights.get("deterministic_base", 0.70))
    semantic = float(weights.get("semantic_relevance", 0.20))
    evidence = float(weights.get("evidence_confidence", 0.10))
    total = deterministic + semantic + evidence
    if total <= 0:
        return 0.7, 0.2, 0.1
    return deterministic / total, semantic / total, evidence / total


def _evidence_is_uncertain(uncertainty: str, unsupported_claims: Iterable[str]) -> bool:
    return bool(uncertainty.strip() or any(str(item).strip() for item in unsupported_claims))


def compute_guarded_score_decision(
    event: SignalEvent,
    *,
    category: SignalCategory,
    analyze: bool,
    source_quality: SourceQuality | str,
    evidence_confidence: float,
    evidence_uncertainty: str,
    evidence_unsupported_claims: Iterable[str],
    semantic_relevance: float,
    project_profile: dict[str, Any],
    policy: dict[str, Any],
    noise_multiplier: float = 1.0,
    seen_hashes: set[str] | None = None,
    feedback_history: Iterable[FeedbackRecord] = (),
) -> GuardedScoreDecision:
    """Score one semantic result while keeping final business policy in Python.

    Semantic Agents can contribute relevance/confidence, but deterministic code owns
    thresholds, evidence-state floors, project applicability, and the final decision.
    """

    deterministic_weight, semantic_weight, evidence_weight = _agent_score_weights(policy)
    base = score_signal(
        event,
        project_profile,
        policy,
        seen_hashes=seen_hashes,
        feedback_history=feedback_history,
        category=category,
    )
    configured_category = float(policy.get("category_weights", {}).get(category.value, 1.0))
    policy_multiplier = (
        0.10
        if category is SignalCategory.NOISE
        else max(0.55, min(1.0, configured_category))
    )
    policy_multiplier *= max(0.0, min(1.0, noise_multiplier))
    deterministic_base_score = min(
        100.0,
        base.final_score / max(base.category_weight, 0.01),
    )
    blended = (
        deterministic_base_score * deterministic_weight
        + semantic_relevance * semantic_weight
        + evidence_confidence * 100 * evidence_weight
    )
    final_score = round(max(0.0, min(100.0, blended * policy_multiplier)), 2)
    quality = source_quality.value if isinstance(source_quality, SourceQuality) else str(source_quality)
    evidence_uncertain = _evidence_is_uncertain(
        evidence_uncertainty, evidence_unsupported_claims
    )
    floors = (
        _priority_floor_score(
            event=event,
            category=category,
            source_quality=quality,
            evidence_uncertain=evidence_uncertain,
            policy=policy,
        ),
        _project_state_priority_floor_score(
            event=event,
            category=category,
            source_quality=quality,
            evidence_uncertain=evidence_uncertain,
            project_profile=project_profile,
            policy=policy,
        ),
        _semantic_priority_floor_score(
            event=event,
            category=category,
            source_quality=quality,
            evidence_confidence=evidence_confidence,
            evidence_uncertain=evidence_uncertain,
            semantic_relevance=semantic_relevance,
            deterministic_base_score=deterministic_base_score,
            policy=policy,
        ),
        _semantic_save_floor_score(
            category=category,
            source_quality=quality,
            evidence_confidence=evidence_confidence,
            semantic_relevance=semantic_relevance,
            policy=policy,
        ),
    )
    for floor in floors:
        if floor is not None:
            final_score = max(final_score, floor)

    decision = decision_for_score(final_score, policy)
    project_state = resolve_project_change_state(event, project_profile)
    importance = project_preference_importance(project_state, project_profile)
    if project_state.already_satisfied or importance == "ignore":
        decision = SignalDecision.IGNORE
    elif project_state.newer_direct_release and decision is SignalDecision.IGNORE:
        decision = SignalDecision.SAVE
    elif (
        category is SignalCategory.COMPETITOR_UPDATE
        and decision is SignalDecision.IGNORE
        and any_affirmed_term(
            f"{event.title} {event.content}".lower(),
            ("maintenance mode", "no new features", "archived", "sunset", "deprecated"),
        )
    ):
        decision = SignalDecision.SAVE
    if category is SignalCategory.NOISE or not analyze:
        decision = SignalDecision.IGNORE

    agent_score = AgentScoreBreakdown(
        deterministic_base_score=round(deterministic_base_score, 2),
        semantic_relevance=semantic_relevance,
        evidence_confidence_score=round(evidence_confidence * 100, 2),
        deterministic_weight=round(deterministic_weight, 4),
        semantic_weight=round(semantic_weight, 4),
        evidence_weight=round(evidence_weight, 4),
        policy_multiplier=round(max(0.0, min(1.0, policy_multiplier)), 4),
        final_score=final_score,
    )
    return GuardedScoreDecision(
        final_score=final_score,
        decision=decision,
        score_breakdown=base,
        agent_score_breakdown=agent_score,
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

    for event in events:
        route = route_by_id[event.event_id]
        evidence_item = evidence_by_id[event.event_id]
        impact_item = impact_by_id[event.event_id]
        action_item = action_by_id[event.event_id]
        noise = noise_by_id.get(event.event_id)
        scored = compute_guarded_score_decision(
            event,
            category=route.category,
            analyze=route.analyze,
            source_quality=evidence_item.source_quality,
            evidence_confidence=evidence_item.confidence,
            evidence_uncertainty=evidence_item.uncertainty,
            evidence_unsupported_claims=evidence_item.unsupported_claims,
            semantic_relevance=impact_item.semantic_relevance,
            project_profile=project_profile,
            policy=policy,
            noise_multiplier=(noise.score_multiplier if noise is not None else 1.0),
            seen_hashes=seen_hashes,
            feedback_history=feedback,
        )

        approval_notes: list[str] = []
        if route.category is SignalCategory.POLICY_SIGNAL:
            action_item = action_item.model_copy(update={"approval_required": True})
        if "action" in route.required_agents and not action_item.requested_actions:
            permission_checks.append(f"{event.event_id}:no_high_risk_actions_requested")
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
        action_items = list(dict.fromkeys([*action_item.action_items, *approval_notes]))
        if action_item.approval_required and action_items:
            action_items.append("Human approval is required before execution.")
        if scored.decision is SignalDecision.IGNORE:
            action_items = []

        reason = _reason_text(
            route.routing_reason,
            route.noise_reason,
            evidence_item.context_summary,
            evidence_item.uncertainty,
            impact_item.impact_reason,
            action_item.critic_notes,
            tool_errors=evidence_item.tool_errors,
        )
        assessments.append(
            SignalAssessment(
                event_id=event.event_id,
                category=route.category,
                relevance_score=impact_item.semantic_relevance,
                impact_score=scored.final_score,
                confidence=evidence_item.confidence,
                affected_modules=impact_item.affected_modules,
                evidence_urls=evidence_item.evidence_urls,
                source_quality=evidence_item.source_quality,
                reason=reason,
                action_items=action_items,
                decision=scored.decision,
                score_breakdown=scored.score_breakdown,
                agent_score_breakdown=scored.agent_score_breakdown,
                related_event_ids=impact_item.related_event_ids,
                cross_source_confidence=impact_item.cross_source_confidence,
                conflicting_evidence=impact_item.conflicting_evidence,
                related_cluster_id=route.related_cluster_id,
                noise_reason=route.noise_reason or (noise.noise_reason if noise else None),
                required_agents=list(route.required_agents),
            )
        )
    return assessments, permission_checks


def _reason_text(*parts: str | None, tool_errors: list[str]) -> str:
    cleaned: list[str] = []
    for part in parts:
        if not part:
            continue
        text = " ".join(str(part).split())
        if not text or _is_tool_debug_text(text):
            continue
        cleaned.append(text)
    if tool_errors and not any("Evidence confidence reduced" in item for item in cleaned):
        cleaned.append("Evidence confidence reduced due to tool errors.")
    return " ".join(dict.fromkeys(cleaned))


def _is_tool_debug_text(text: str) -> bool:
    lowered = text.lower()
    debug_markers = (
        "rss parse failed",
        "rss request failed",
        "github request failed",
        "tool failures or blocked requests",
        "syntax error: line",
    )
    return any(marker in lowered for marker in debug_markers)


def _project_state_priority_floor_score(
    *,
    event: SignalEvent,
    category: SignalCategory,
    source_quality: str,
    evidence_uncertain: bool = False,
    project_profile: dict[str, Any],
    policy: dict[str, Any],
) -> float | None:
    """Protect project-specific protocol/dependency risks generic scoring can under-rate."""

    config = policy.get("project_state_priority_floor", {})
    if isinstance(config, dict) and config.get("enabled") is False:
        return None
    suppress = True if not isinstance(config, dict) else bool(
        config.get("suppress_on_uncertainty", True)
    )
    if evidence_uncertain and suppress:
        return None
    state = resolve_project_change_state(event, project_profile)
    if state.already_satisfied or source_quality == "unverified":
        return None
    text = f"{event.title} {event.content}".lower()
    direct_terms = ("migration", "compatibility", "schema", "oauth", "transport")
    protocol_terms = (
        "allowlist",
        "path traversal",
        "structuredcontent",
        "structured content",
        "outputschema",
        "output schema",
        "session",
        "initialize",
        "streamable http",
        "oauth",
        "routing",
    )
    direct_risk = (
        state.newer_direct_release
        and category is SignalCategory.DEPENDENCY_UPDATE
        and any_affirmed_term(text, direct_terms)
    )
    protocol_risk = (
        state.protocol_name is not None
        and category
        in {
            SignalCategory.POLICY_SIGNAL,
            SignalCategory.AGENT_RUNTIME_SIGNAL,
            SignalCategory.STRUCTURED_OUTPUT_SIGNAL,
        }
        and any_affirmed_term(text, protocol_terms)
    )
    if not direct_risk and not protocol_risk:
        return None
    thresholds = policy.get("thresholds", {})
    return max(0.0, min(100.0, float(thresholds.get("alert", 80.0))))


def _priority_floor_score(
    *,
    event: SignalEvent,
    category: SignalCategory,
    source_quality: str,
    evidence_uncertain: bool = False,
    policy: dict[str, Any],
) -> float | None:
    """Protect explicit high-severity primary-source signals from model under-scoring."""

    config = policy.get("priority_floor", {})
    if not isinstance(config, dict) or not config.get("enabled", False):
        return None
    if evidence_uncertain and bool(config.get("suppress_on_uncertainty", True)):
        return None
    categories = {str(value) for value in config.get("categories", [])}
    if category.value not in categories:
        return None
    if bool(config.get("official_only", True)) and source_quality != "official":
        return None
    text = f"{event.title} {event.content}".lower()
    terms = [str(value).strip().lower() for value in config.get("terms", [])]
    if not any_affirmed_term(text, (term for term in terms if term)):
        return None
    return max(0.0, min(100.0, float(config.get("min_score", 80.0))))


def _semantic_priority_floor_score(
    *,
    event: SignalEvent,
    category: SignalCategory,
    source_quality: str,
    evidence_confidence: float,
    evidence_uncertain: bool,
    semantic_relevance: float,
    deterministic_base_score: float,
    policy: dict[str, Any],
) -> float | None:
    """Lift verified, direct project-impact changes without trusting model final scores."""

    config = policy.get("semantic_priority_floor", {})
    if not isinstance(config, dict) or not config.get("enabled", False):
        return None
    if evidence_uncertain and bool(config.get("suppress_on_uncertainty", True)):
        return None
    categories = {str(value) for value in config.get("categories", [])}
    if category.value not in categories:
        return None
    if bool(config.get("official_only", True)) and source_quality != "official":
        return None
    strong_semantic = semantic_relevance >= float(
        config.get("min_semantic_relevance", 88.0)
    )
    corroborated = (
        semantic_relevance
        >= float(config.get("corroborated_min_semantic_relevance", 80.0))
        and deterministic_base_score
        >= float(config.get("min_deterministic_base_score", 80.0))
    )
    if not strong_semantic and not corroborated:
        return None
    if evidence_confidence < float(config.get("min_evidence_confidence", 0.85)):
        return None
    if event.source_type == "github_issue" and bool(
        config.get("suppress_discussion_issues", True)
    ):
        text = f"{event.title} {event.content}".lower()
        discussion_terms = tuple(
            str(value).strip().lower()
            for value in config.get(
                "discussion_terms",
                [
                    "proposal",
                    "not merged",
                    "unmerged",
                    "still open",
                    "under discussion",
                    "maintainers discuss",
                    "rfc",
                    "draft proposal",
                ],
            )
            if str(value).strip()
        )
        if any(term in text for term in discussion_terms):
            return None
    terms = [str(value).strip().lower() for value in config.get("terms", [])]
    text = f"{event.title} {event.content}".lower()
    if not any_affirmed_term(text, (term for term in terms if term)):
        return None
    return max(0.0, min(100.0, float(config.get("min_score", 80.0))))


def _semantic_save_floor_score(
    *,
    category: SignalCategory,
    source_quality: str,
    evidence_confidence: float,
    semantic_relevance: float,
    policy: dict[str, Any],
) -> float | None:
    """Retain high-relevance engineering guidance without escalating it to an alert."""

    config = policy.get("semantic_save_floor", {})
    if not isinstance(config, dict) or not config.get("enabled", False):
        return None
    categories = {str(value) for value in config.get("categories", [])}
    if category.value not in categories:
        return None
    qualities = {str(value) for value in config.get("source_qualities", [])}
    if qualities and source_quality not in qualities:
        return None
    if semantic_relevance < float(config.get("min_semantic_relevance", 80.0)):
        return None
    if evidence_confidence < float(config.get("min_evidence_confidence", 0.55)):
        return None
    configured = float(config.get("min_score", policy.get("thresholds", {}).get("save", 45.0)))
    save_threshold = float(policy.get("thresholds", {}).get("save", 45.0))
    alert_threshold = float(policy.get("thresholds", {}).get("alert", 80.0))
    return max(save_threshold, min(configured, max(save_threshold, alert_threshold - 0.01)))
