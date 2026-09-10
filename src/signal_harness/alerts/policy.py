"""Deterministic alert policy for local-only major-event notifications."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from signal_harness.presentation import sanitize_user_facing_actions
from signal_harness.signal.schemas import (
    SignalAssessment,
    SignalCategory,
    SignalDecision,
    SignalEvent,
    SourceQuality,
)

IMPORTANT_MODULE_TERMS = {
    "core",
    "provider",
    "tool",
    "tools",
    "permission",
    "permissions",
    "security",
}
IMPORTANT_CATEGORIES = {
    SignalCategory.DEPENDENCY_UPDATE,
    SignalCategory.CHECKPOINT_PERSISTENCE_SIGNAL,
    SignalCategory.STRUCTURED_OUTPUT_SIGNAL,
    SignalCategory.TOOL_CALLING_SIGNAL,
    SignalCategory.PROVIDER_COMPATIBILITY_SIGNAL,
    SignalCategory.SECURITY_SUPPLY_CHAIN,
    SignalCategory.EVALUATION_BENCHMARK_SIGNAL,
    SignalCategory.SOURCE_COLLECTION_SIGNAL,
    SignalCategory.POLICY_SIGNAL,
}
ORDINARY_RELEASE_IMPACT_TERMS = {
    "breaking",
    "security",
    "cve",
    "vulnerability",
    "deprecated",
    "deprecation",
    "supply chain",
    "compatibility",
    "regression",
    "migration",
    "api change",
    "api compatibility",
    "schema",
    "validation",
    "json schema compatibility",
}


@dataclass(frozen=True)
class AlertPolicy:
    """Simple deterministic policy for local alert artifacts."""

    alert_threshold: float = 75.0
    official_confidence_threshold: float = 0.75
    cross_source_threshold: float = 0.70
    max_alerts_per_category: int = 3

    @classmethod
    def from_signal_policy(cls, policy: dict[str, Any]) -> "AlertPolicy":
        thresholds = policy.get("thresholds", {})
        raw_threshold = thresholds.get("alert", 75.0)
        try:
            alert_threshold = float(raw_threshold)
        except (TypeError, ValueError):
            alert_threshold = 75.0
        return cls(alert_threshold=alert_threshold)


def select_alerts(
    events: list[SignalEvent],
    assessments: list[SignalAssessment],
    *,
    policy: AlertPolicy,
    already_alerted: set[str] | None = None,
) -> list[dict[str, Any]]:
    """Return new alert records; never dispatch externally."""

    event_by_id = {event.event_id: event for event in events}
    seen = already_alerted or set()
    alerts: list[dict[str, Any]] = []
    category_counts: dict[SignalCategory, int] = {}
    for assessment in assessments:
        if assessment.event_id in seen:
            continue
        event = event_by_id.get(assessment.event_id)
        reasons = _alert_reasons(assessment, policy, event=event)
        if not reasons:
            continue
        current_count = category_counts.get(assessment.category, 0)
        if current_count >= policy.max_alerts_per_category:
            continue
        category_counts[assessment.category] = current_count + 1
        alerts.append(
            {
                "event_id": assessment.event_id,
                "title": event.title if event else assessment.event_id,
                "source_name": event.source_name if event else "",
                "source_type": event.source_type if event else "",
                "url": event.url if event else "",
                "decision": assessment.decision.value,
                "category": assessment.category.value,
                "impact_score": assessment.impact_score,
                "confidence": assessment.confidence,
                "affected_modules": assessment.affected_modules,
                "cross_source_confidence": assessment.cross_source_confidence,
                "conflicting_evidence": assessment.conflicting_evidence,
                "reasons": reasons,
                "what_changed_zh": assessment.what_changed_zh,
                "why_relevant_zh": assessment.why_relevant_zh,
                "recommended_actions_zh": sanitize_user_facing_actions(
                    assessment.action_items_zh
                ),
                "external_dispatch": "disabled",
            }
        )
    return alerts


def _alert_reasons(
    assessment: SignalAssessment,
    policy: AlertPolicy,
    *,
    event: SignalEvent | None,
) -> list[str]:
    reasons: list[str] = []
    if assessment.decision is SignalDecision.ACTION_REQUIRED:
        reasons.append("decision=action_required")
    elif (
        assessment.decision is SignalDecision.ALERT
        and assessment.impact_score >= policy.alert_threshold
        and assessment.category in IMPORTANT_CATEGORIES
        and not _is_ordinary_release_series(assessment, event)
    ):
        reasons.extend(
            [
                "decision=alert",
                f"impact_score>={policy.alert_threshold:g}",
                f"high_risk_category={assessment.category.value}",
            ]
        )
    else:
        return []
    if (
        assessment.source_quality is SourceQuality.OFFICIAL
        and assessment.confidence >= policy.official_confidence_threshold
    ):
        reasons.append("official_source_high_confidence")
    if assessment.cross_source_confidence >= policy.cross_source_threshold:
        reasons.append("cross_source_confidence_high")
    if assessment.conflicting_evidence:
        reasons.append("conflicting_evidence_requires_review")
    matched_modules = [
        module
        for module in assessment.affected_modules
        if any(term in module.lower() for term in IMPORTANT_MODULE_TERMS)
    ]
    if matched_modules:
        reasons.append("important_affected_module=" + ",".join(matched_modules[:3]))
    return reasons


def _is_ordinary_release_series(
    assessment: SignalAssessment,
    event: SignalEvent | None,
) -> bool:
    if event is None:
        return False
    if event.source_type != "github_release":
        return False
    if assessment.category is not SignalCategory.DEPENDENCY_UPDATE:
        return False
    text = f"{event.source_name} {event.title} {event.content}".lower()
    return not any(term in text for term in ORDINARY_RELEASE_IMPACT_TERMS)
