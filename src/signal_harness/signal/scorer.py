"""Deterministic, inspectable SignalHarness scoring."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Iterable

from signal_harness.signal.project_state import resolve_project_change_state
from signal_harness.signal.schemas import (
    FeedbackRecord,
    ScoreBreakdown,
    SignalCategory,
    SignalEvent,
)
from signal_harness.signal.source_authority import event_source_quality
from signal_harness.signal.text_semantics import (
    any_affirmed_term,
    contains_affirmed_term,
    source_semantic_text,
)

DEPENDENCY_IMPACT_TERMS = (
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
)


def _bounded(value: float) -> float:
    return round(max(0.0, min(100.0, value)), 2)


def _keywords(profile: dict[str, Any], key: str) -> list[str]:
    values = profile.get(key, [])
    return [str(value).strip().lower() for value in values if str(value).strip()]


def _matched(text: str, keywords: Iterable[str]) -> list[str]:
    return [keyword for keyword in keywords if contains_affirmed_term(text, keyword)]


def _semantic_text(event: SignalEvent, *, include_source: bool = False) -> str:
    """Return source-aware deterministic matching text."""

    return source_semantic_text(
        source_type=event.source_type,
        source_name=event.source_name,
        title=event.title,
        content=event.content,
        include_source=include_source,
    )


def _has_dependency_impact_terms(event: SignalEvent) -> bool:
    return any_affirmed_term(
        _semantic_text(event, include_source=True),
        DEPENDENCY_IMPACT_TERMS,
    )


def source_score(event: SignalEvent, policy: dict[str, Any]) -> float:
    """Score source authority from event type and explicit official metadata."""

    source_weights = policy.get("source_weights", {})
    quality = event_source_quality(event)
    if event.source_type in {"github_release", "package_registry", "security_advisory"}:
        key = "official_release" if quality.value == "official" else "community_discussion"
    elif event.source_type in {"github_commit", "github_pull_request", "local_git_commit"}:
        key = "team_update"
    elif event.source_type == "github_issue":
        if quality.value == "official":
            key = "official_issue"
        elif quality.value == "maintainer":
            key = "maintainer_issue"
        else:
            key = "community_discussion"
    elif event.source_type == "rss":
        key = "official_blog" if quality.value == "official" else "expert_blog"
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
    text = _semantic_text(event)
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


def _preference_relevance_adjustment(
    event: SignalEvent, profile: dict[str, Any]
) -> float:
    text = _semantic_text(event, include_source=True)
    state = resolve_project_change_state(event, profile)
    entity_keys = {
        "dependency": state.dependency_name,
        "provider": state.provider_name,
        "protocol": state.protocol_name,
        "runtime": state.runtime_name,
    }
    preferences = profile.get("importance_preferences", [])
    if not isinstance(preferences, list):
        return 0.0
    weights = {
        "critical": 35.0,
        "important": 18.0,
        "normal": 0.0,
        "low": -25.0,
        "ignore": -100.0,
    }
    adjustment = 0.0
    for item in preferences:
        if not isinstance(item, dict):
            continue
        scope_type = str(item.get("scope_type") or "topic").strip().lower()
        scope_key = str(item.get("scope_key") or "").strip()
        if not scope_key:
            continue
        entity_name = entity_keys.get(scope_type)
        if entity_name is not None:
            matched = _normalized_preference_key(scope_key) == _normalized_preference_key(entity_name)
        elif scope_type in entity_keys:
            matched = False
        else:
            matched = contains_affirmed_term(text, scope_key.lower())
        if not matched:
            continue
        importance = str(item.get("importance") or "normal").lower()
        value = weights.get(importance, 0.0)
        if importance == "ignore":
            return value
        adjustment += value
    return max(-100.0, min(45.0, adjustment))


def _normalized_preference_key(value: str) -> str:
    return " ".join(value.strip().lower().replace("_", " ").replace("-", " ").split())


def relevance_score(
    event: SignalEvent,
    profile: dict[str, Any],
    policy: dict[str, Any] | None = None,
) -> float:
    """Measure overlap with stack, dependencies, competitors, and critical modules."""

    active_policy = policy or {}
    text = _semantic_text(event, include_source=True)
    groups = (
        ("critical_modules", 30),
        ("monitored_ecosystem", 12),
        ("tech_stack", 15),
        ("competitors", 10),
        ("focus_keywords", 15),
    )
    score = 10.0
    state = resolve_project_change_state(event, profile)
    if state.direct_dependency:
        score += 28
    if state.provider_name or state.protocol_name or state.runtime_name:
        score += 24
    if event.raw_payload.get("project_owned") is True:
        score += 35
    for key, points in groups:
        if _matched(text, _keywords(profile, key)):
            score += points
    if state.already_satisfied:
        score -= 45
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
    score += _preference_relevance_adjustment(event, profile)
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
    text = _semantic_text(event)
    if any_affirmed_term(text, ("security", "breaking", "deprecated", "urgent", "migration")):
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
    category_name = category.value if isinstance(category, SignalCategory) else str(category or "")
    configured_weight = float(policy.get("category_weights", {}).get(category_name, 1.0))
    category_weight = (
        0.10
        if category_name == SignalCategory.NOISE.value
        else max(0.55, min(1.0, configured_weight))
    )
    if category_name == SignalCategory.DEPENDENCY_UPDATE.value and not _has_dependency_impact_terms(
        event
    ):
        category_weight = min(category_weight, 0.82)
    final = weighted_score * category_weight
    return ScoreBreakdown(
        **components,
        category_weight=round(category_weight, 4),
        final_score=_bounded(final),
    )
