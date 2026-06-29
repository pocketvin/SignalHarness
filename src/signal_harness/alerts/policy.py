"""Deterministic alert policy for local-only major-event notifications."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

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
    SignalCategory.POLICY_SIGNAL,
}


@dataclass(frozen=True)
class AlertPolicy:
    """Simple deterministic policy for local alert artifacts."""

    alert_threshold: float = 75.0
    official_confidence_threshold: float = 0.75
    cross_source_threshold: float = 0.70

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
    for assessment in assessments:
        if assessment.event_id in seen:
            continue
        reasons = _alert_reasons(assessment, policy)
        if not reasons:
            continue
        event = event_by_id.get(assessment.event_id)
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
                "external_dispatch": "disabled",
            }
        )
    return alerts


def _alert_reasons(
    assessment: SignalAssessment,
    policy: AlertPolicy,
) -> list[str]:
    reasons: list[str] = []
    if assessment.decision is SignalDecision.ACTION_REQUIRED:
        reasons.append("decision=action_required")
    if assessment.decision is SignalDecision.ALERT:
        reasons.append("decision=alert")
    if assessment.impact_score >= policy.alert_threshold:
        reasons.append(f"impact_score>={policy.alert_threshold:g}")
    if assessment.category in IMPORTANT_CATEGORIES:
        reasons.append(f"important_category={assessment.category.value}")
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

