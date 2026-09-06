"""Cheap project-aware candidate funnel before expensive Agent execution."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from signal_harness.signal.deduplicator import signal_fingerprint
from signal_harness.signal.schemas import SignalEvent
from signal_harness.signal.scorer import keyword_score, relevance_score, source_score, urgency_score


@dataclass(frozen=True)
class CandidateFunnelResult:
    events: list[SignalEvent]
    metadata: dict[str, Any]


@dataclass(frozen=True)
class _RankedCandidate:
    event: SignalEvent
    index: int
    score: float


def candidate_score(
    event: SignalEvent,
    project_profile: dict[str, Any],
    policy: dict[str, Any],
    *,
    seen_hashes: set[str] | None = None,
    now: datetime | None = None,
) -> float:
    """Rank cheaply from project overlap, keywords, provenance, recency, and novelty."""

    novelty = 40.0 if seen_hashes and signal_fingerprint(event) in seen_hashes else 100.0
    value = (
        relevance_score(event, project_profile, policy) * 0.45
        + keyword_score(event, project_profile, policy) * 0.20
        + source_score(event, policy) * 0.15
        + urgency_score(event, now=now) * 0.10
        + novelty * 0.10
    )
    return round(max(0.0, min(100.0, value)), 2)


def select_candidates(
    events: list[SignalEvent],
    *,
    project_profile: dict[str, Any],
    policy: dict[str, Any],
    max_events: int | None,
    max_events_per_source: int | None,
    seen_hashes: set[str] | None = None,
    now: datetime | None = None,
) -> CandidateFunnelResult:
    """Select a relevance-first shortlist while reserving some source diversity."""

    ranked = [
        _RankedCandidate(
            event=event,
            index=index,
            score=candidate_score(event, project_profile, policy, seen_hashes=seen_hashes, now=now),
        )
        for index, event in enumerate(events)
    ]
    before_by_source = _counts(ranked)
    selected = ranked
    if max_events_per_source is not None:
        grouped: dict[str, list[_RankedCandidate]] = {}
        for item in selected:
            grouped.setdefault(_source_key(item.event), []).append(item)
        selected = [
            item
            for key in sorted(grouped)
            for item in sorted(grouped[key], key=_rank_key)[:max_events_per_source]
        ]

    if max_events is not None and len(selected) > max_events:
        selected = _select_with_diversity(selected, max_events)
    else:
        selected = sorted(selected, key=_rank_key)

    after_by_source = _counts(selected)
    scores = [item.score for item in selected]
    return CandidateFunnelResult(
        events=[item.event for item in selected],
        metadata={
            "strategy": "project_relevance_v2",
            "before_count": len(events),
            "after_count": len(selected),
            "dropped_count": max(0, len(events) - len(selected)),
            "max_events": max_events,
            "max_events_per_source": max_events_per_source,
            "source_counts_before": before_by_source,
            "source_counts_after": after_by_source,
            "selected_score_min": min(scores) if scores else None,
            "selected_score_max": max(scores) if scores else None,
        },
    )


def _select_with_diversity(
    ranked: list[_RankedCandidate],
    max_events: int,
) -> list[_RankedCandidate]:
    ordered = sorted(ranked, key=_rank_key)
    relevance_slots = max(1, max_events - max(1, max_events // 3))
    chosen = ordered[:relevance_slots]
    chosen_ids = {item.index for item in chosen}
    represented = {_source_key(item.event) for item in chosen}

    diversity_pool = [
        item
        for item in ordered
        if item.index not in chosen_ids and _source_key(item.event) not in represented
    ]
    for item in diversity_pool:
        if len(chosen) >= max_events:
            break
        chosen.append(item)
        chosen_ids.add(item.index)
        represented.add(_source_key(item.event))

    for item in ordered:
        if len(chosen) >= max_events:
            break
        if item.index not in chosen_ids:
            chosen.append(item)
            chosen_ids.add(item.index)
    return sorted(chosen, key=_rank_key)


def _rank_key(item: _RankedCandidate) -> tuple[float, float, int]:
    published = item.event.published_at
    timestamp = published.timestamp() if published is not None else 0.0
    return (-item.score, -timestamp, item.index)


def _source_key(event: SignalEvent) -> str:
    return f"{event.source_type}:{event.source_name}"


def _counts(items: list[_RankedCandidate]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for item in items:
        key = _source_key(item.event)
        counts[key] = counts.get(key, 0) + 1
    return dict(sorted(counts.items()))
