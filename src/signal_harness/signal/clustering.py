"""Rule-based multi-source signal clustering."""

from __future__ import annotations

import hashlib
import re
from collections import defaultdict
from datetime import timezone
from urllib.parse import urlparse

from signal_harness.signal.schemas import SignalCluster, SignalEvent

STOP_WORDS = {
    "about",
    "after",
    "before",
    "from",
    "into",
    "that",
    "their",
    "this",
    "with",
    "update",
}


def _tokens(event: SignalEvent) -> set[str]:
    return {
        token
        for token in re.findall(
            r"[a-z][a-z0-9_-]{3,}",
            f"{event.source_name} {event.title} {event.content}".lower(),
        )
        if token not in STOP_WORDS
    }


def _domain(event: SignalEvent) -> str:
    return urlparse(event.url).netloc.lower()


class SignalClusterer:
    """Group related events using source, domain, token, and time overlap."""

    def cluster(self, events: list[SignalEvent]) -> list[SignalCluster]:
        if not events:
            return []
        parents = list(range(len(events)))
        event_tokens = [_tokens(event) for event in events]

        def find(index: int) -> int:
            while parents[index] != index:
                parents[index] = parents[parents[index]]
                index = parents[index]
            return index

        def union(left: int, right: int) -> None:
            left_root, right_root = find(left), find(right)
            if left_root != right_root:
                parents[right_root] = left_root

        for left, first in enumerate(events):
            for right in range(left + 1, len(events)):
                second = events[right]
                shared = event_tokens[left] & event_tokens[right]
                smaller = min(len(event_tokens[left]), len(event_tokens[right])) or 1
                overlap = len(shared) / smaller
                same_source = first.source_name.lower() == second.source_name.lower()
                same_domain = bool(_domain(first)) and _domain(first) == _domain(second)
                close_in_time = self._close_in_time(first, second)
                if same_source or same_domain or (overlap >= 0.28 and close_in_time):
                    union(left, right)

        groups: dict[int, list[SignalEvent]] = defaultdict(list)
        for index, event in enumerate(events):
            groups[find(index)].append(event)

        clusters: list[SignalCluster] = []
        for grouped in groups.values():
            all_tokens: dict[str, int] = defaultdict(int)
            for event in grouped:
                for token in _tokens(event):
                    all_tokens[token] += 1
            topic = max(
                all_tokens,
                key=lambda token: (all_tokens[token], len(token)),
                default=grouped[0].source_name,
            )
            source_types = sorted({event.source_type for event in grouped})
            confidence = (
                0.35
                if len(grouped) == 1
                else min(
                    0.95,
                    0.35
                    + 0.15 * min(len(grouped), 3)
                    + 0.15 * min(len(source_types), 3),
                )
            )
            event_ids = [event.event_id for event in grouped]
            digest = hashlib.sha256("|".join(sorted(event_ids)).encode()).hexdigest()[:12]
            clusters.append(
                SignalCluster(
                    cluster_id=f"cluster-{digest}",
                    topic=topic,
                    related_event_ids=event_ids,
                    entities=sorted(
                        {event.source_name for event in grouped}
                        | {
                            token
                            for token, count in all_tokens.items()
                            if count >= 2
                        }
                    )[:12],
                    time_window=self._time_window(grouped),
                    source_types=source_types,
                    confidence=round(confidence, 3),
                )
            )
        return clusters

    @staticmethod
    def _close_in_time(first: SignalEvent, second: SignalEvent) -> bool:
        if first.published_at is None or second.published_at is None:
            return True
        left = first.published_at
        right = second.published_at
        if left.tzinfo is None:
            left = left.replace(tzinfo=timezone.utc)
        if right.tzinfo is None:
            right = right.replace(tzinfo=timezone.utc)
        return abs((left - right).days) <= 14

    @staticmethod
    def _time_window(events: list[SignalEvent]) -> str:
        dates = sorted(
            event.published_at.isoformat()
            for event in events
            if event.published_at is not None
        )
        if not dates:
            return "undated"
        return dates[0] if len(dates) == 1 else f"{dates[0]}..{dates[-1]}"
