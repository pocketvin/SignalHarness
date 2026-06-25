"""Deterministic, inspectable SignalHarness scoring."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Iterable

from signal_harness.signal.schemas import (
    FeedbackRecord,
    ScoreBreakdown,
    SignalCategory,
    SignalEvent,
)


def _bounded(value: float) -> float:
    return round(max(0.0, min(100.0, value)), 2)


def _keywords(profile: dict[str, Any], key: str) -> list[str]:
    values = profile.get(key, [])
    return [str(value).strip().lower() for value in values if str(value).strip()]


def _matched(text: str, keywords: Iterable[str]) -> list[str]:
    return [keyword for keyword in keywords if keyword in text]


def source_score(event: SignalEvent, policy: dict[str, Any]) -> float:
    """Score source authority from event type and explicit official metadata."""

    source_weights = policy.get("source_weights", {})
    official = bool(event.raw_payload.get("official"))
    if event.source_type == "github_release":
        key = "official_release" if official else "community_discussion"
    elif event.source_type == "github_issue":
        key = "official_issue" if official else "community_discussion"
    elif event.source_type == "rss":
        key = "official_blog" if official else "expert_blog"
    elif event.source_type == "web_change":
        key = "web_change"
    elif event.source_type == "team_update":
        key = "team_update"
    else:
        key = "unverified"
    return _bounded(float(source_weights.get(key, source_weights.get("unverified", 20))))


def _policy_keywords(policy: dict[str, Any]) -> tuple[list[str], dict[str, float]]:
    suggested = [
        str(value).strip().lower()
        for value in policy.get("suggested_focus_keywords", [])
        if str(value).strip()
    ]
    raw_weights = policy.get("keyword_weights", {})
    weights = (
        {
            str(key).strip().lower(): float(value)
            for key, value in raw_weights.items()
            if str(key).strip()
        }
        if isinstance(raw_weights, dict)
        else {}
    )
    return suggested, weights


def keyword_score(
    event: SignalEvent,
    profile: dict[str, Any],
    policy: dict[str, Any] | None = None,
) -> float:
    """Reward focus-keyword matches and penalize explicit ignore terms."""

    active_policy = policy or {}
    text = f"{event.title} {event.content}".lower()
    suggested, keyword_weights = _policy_keywords(active_policy)
    focus_terms = list(
        dict.fromkeys(
            [
                *_keywords(profile, "focus_keywords"),
                *suggested,
                *keyword_weights,
            ]
        )
    )
    focus_hits = _matched(text, focus_terms)
    ignore_terms = [
        *_keywords(profile, "ignore_keywords"),
        *[
            str(value).strip().lower()
            for value in active_policy.get("ignore_patterns", [])
            if str(value).strip()
        ],
    ]
    ignore_hits = _matched(text, ignore_terms)
    weighted_points = sum(22.0 * keyword_weights.get(keyword, 1.0) for keyword in focus_hits)
    score = 20 + min(80, weighted_points) - min(90, len(ignore_hits) * 55)
    return _bounded(score)


def relevance_score(
    event: SignalEvent,
    profile: dict[str, Any],
    policy: dict[str, Any] | None = None,
) -> float:
    """Measure overlap with stack, dependencies, competitors, and critical modules."""

    active_policy = policy or {}
    text = f"{event.source_name} {event.title} {event.content}".lower()
    groups = (
        ("critical_modules", 30),
        ("dependencies", 30),
        ("tech_stack", 15),
        ("competitors", 20),
        ("focus_keywords", 15),
    )
    score = 10.0
    for key, points in groups:
        if _matched(text, _keywords(profile, key)):
            score += points
    suggested, keyword_weights = _policy_keywords(active_policy)
    adaptive_hits = _matched(text, list(dict.fromkeys([*suggested, *keyword_weights])))
    score += min(
        25.0,
        sum(10.0 * keyword_weights.get(keyword, 1.0) for keyword in adaptive_hits),
    )
    ignore_hits = _matched(
        text,
        [
            str(value).strip().lower()
            for value in active_policy.get("ignore_patterns", [])
            if str(value).strip()
        ],
    )
    score -= min(60.0, len(ignore_hits) * 40.0)
    return _bounded(score)


def novelty_score(event: SignalEvent, seen_hashes: set[str] | None = None) -> float:
    """Return a high novelty score unless a fingerprint was previously observed."""

    if not seen_hashes:
        return 100.0
    from signal_harness.signal.deduplicator import signal_fingerprint

    return 40.0 if signal_fingerprint(event) in seen_hashes else 100.0


def urgency_score(event: SignalEvent, *, now: datetime | None = None) -> float:
    """Score recency and urgency vocabulary without using an LLM."""

    current = now or datetime.now(timezone.utc)
    base = 35.0
    if event.published_at is not None:
        published = event.published_at
        if published.tzinfo is None:
            published = published.replace(tzinfo=timezone.utc)
        age_days = max(0, (current - published).days)
        base = max(10.0, 100.0 - age_days * 5)
    text = f"{event.title} {event.content}".lower()
    if any(word in text for word in ("security", "breaking", "deprecated", "urgent", "migration")):
        base += 15
    return _bounded(base)


def feedback_score(
    event: SignalEvent,
    policy: dict[str, Any],
    feedback_history: Iterable[FeedbackRecord] = (),
) -> float:
    """Convert historical judgments into a bounded future-score component."""

    adjustments = policy.get("feedback_adjustments", {})
    value = 50.0
    for record in feedback_history:
        if record.event_id != event.event_id:
            continue
        value += float(adjustments.get(record.feedback.value, 0))
    return _bounded(value)


def score_signal(
    event: SignalEvent,
    project_profile: dict[str, Any],
    policy: dict[str, Any],
    *,
    seen_hashes: set[str] | None = None,
    feedback_history: Iterable[FeedbackRecord] = (),
    now: datetime | None = None,
    category: SignalCategory | str | None = None,
) -> ScoreBreakdown:
    """Calculate all score components and their weighted final score."""

    components = {
        "source_weight": source_score(event, policy),
        "keyword_match_score": keyword_score(event, project_profile, policy),
        "project_relevance_score": relevance_score(event, project_profile, policy),
        "novelty_score": novelty_score(event, seen_hashes),
        "urgency_score": urgency_score(event, now=now),
        "feedback_adjustment": feedback_score(event, policy, feedback_history),
    }
    weights = policy["score_weights"]
    weighted_score = sum(components[name] * float(weights[name]) for name in components)
    category_name = (
        category.value if isinstance(category, SignalCategory) else str(category or "")
    )
    configured_weight = float(policy.get("category_weights", {}).get(category_name, 1.0))
    category_weight = (
        0.10
        if category_name == SignalCategory.NOISE.value
        else max(0.85, min(1.0, 0.85 + 0.15 * configured_weight))
    )
    final = weighted_score * category_weight
    return ScoreBreakdown(
        **components,
        category_weight=round(category_weight, 4),
        final_score=_bounded(final),
    )
