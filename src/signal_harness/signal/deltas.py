"""Source-native change metadata without pretending to have unavailable diffs."""

from __future__ import annotations

from collections import defaultdict

from signal_harness.signal.schemas import SignalEvent


def annotate_release_lineage(events: list[SignalEvent]) -> list[SignalEvent]:
    """Infer previous release versions from releases observed in the same collection batch."""

    by_source: dict[str, list[SignalEvent]] = defaultdict(list)
    for event in events:
        if (
            event.source_type == "github_release"
            and event.current_version
            and event.previous_version is None
        ):
            by_source[event.source_name].append(event)

    previous_by_id: dict[str, str] = {}
    for releases in by_source.values():
        ordered = sorted(
            releases,
            key=lambda event: event.published_at.timestamp() if event.published_at else 0.0,
            reverse=True,
        )
        for current, previous in zip(ordered, ordered[1:]):
            if previous.current_version:
                previous_by_id[current.event_id] = previous.current_version

    return [
        event.model_copy(update={"previous_version": previous_by_id[event.event_id]})
        if event.event_id in previous_by_id
        else event
        for event in events
    ]
