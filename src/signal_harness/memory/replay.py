"""Deterministic replay comparison for policy proposals."""

from __future__ import annotations

from typing import Any, Iterable

from signal_harness.agent_integration.schemas import ReplayEvaluation
from signal_harness.signal.scorer import score_signal
from signal_harness.signal.schemas import FeedbackLabel, FeedbackRecord, SignalEvent

POSITIVE_LABELS = {FeedbackLabel.USEFUL, FeedbackLabel.MISSED_SIGNAL}
NEGATIVE_LABELS = {
    FeedbackLabel.NOT_USEFUL,
    FeedbackLabel.FALSE_POSITIVE,
    FeedbackLabel.TOO_GENERIC,
}


def _policy_outcomes(
    events: dict[str, SignalEvent],
    feedback: Iterable[FeedbackRecord],
    profile: dict[str, Any],
    policy: dict[str, Any],
) -> tuple[float, int, int]:
    true_positive = 0
    false_positive = 0
    missed = 0
    threshold = float(policy.get("thresholds", {}).get("save", 45))
    for record in feedback:
        event = events.get(record.event_id)
        if event is None:
            if record.feedback is FeedbackLabel.MISSED_SIGNAL:
                missed += 1
            continue
        predicted = score_signal(event, profile, policy).final_score >= threshold
        if record.feedback in POSITIVE_LABELS:
            if predicted:
                true_positive += 1
            else:
                missed += 1
        elif record.feedback in NEGATIVE_LABELS and predicted:
            false_positive += 1
    denominator = true_positive + false_positive
    precision = true_positive / denominator if denominator else 0.0
    return precision, false_positive, missed


def evaluate_policy_replay(
    events: Iterable[SignalEvent],
    feedback: Iterable[FeedbackRecord],
    *,
    project_profile: dict[str, Any],
    old_policy: dict[str, Any],
    proposed_policy: dict[str, Any],
) -> ReplayEvaluation:
    """Compare old and proposed policy behavior on labeled historical signals."""

    event_map = {event.event_id: event for event in events}
    feedback_list = list(feedback)
    old_precision, old_false_positive, old_missed = _policy_outcomes(
        event_map,
        feedback_list,
        project_profile,
        old_policy,
    )
    new_precision, new_false_positive, new_missed = _policy_outcomes(
        event_map,
        feedback_list,
        project_profile,
        proposed_policy,
    )
    false_positive_reduction = old_false_positive - new_false_positive
    missed_signal_reduction = old_missed - new_missed
    recommendation = (
        "review-and-consider"
        if (
            new_precision >= old_precision
            and false_positive_reduction >= 0
            and missed_signal_reduction >= 0
        )
        else "reject-or-revise"
    )
    return ReplayEvaluation(
        old_precision_proxy=round(old_precision, 4),
        new_precision_proxy=round(new_precision, 4),
        false_positive_reduction=false_positive_reduction,
        missed_signal_reduction=missed_signal_reduction,
        recommendation=recommendation,
    )
