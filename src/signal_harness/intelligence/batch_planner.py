"""Deterministic, diversity-aware shallow batching."""

from __future__ import annotations

import json
from collections import defaultdict, deque

from signal_harness.intelligence.fact_capsule import capsule_semantic_payload
from signal_harness.intelligence.semantic_router import SemanticWorkItem


class BalancedBatchPlanner:
    def __init__(self, *, max_items: int, max_input_bytes: int) -> None:
        self.max_items = max_items
        self.max_input_bytes = max_input_bytes

    def plan(self, items: list[SemanticWorkItem]) -> list[list[SemanticWorkItem]]:
        """Round-robin entities before packing, reducing same-entity priming when possible."""
        by_entity: dict[str, deque[SemanticWorkItem]] = defaultdict(deque)
        for item in sorted(
            items,
            key=lambda value: (
                value.capsule.entity.casefold(),
                value.capsule.kind,
                value.capsule.published_at or "",
                value.capsule.change_id,
            ),
        ):
            by_entity[item.capsule.entity.casefold()].append(item)

        interleaved: list[SemanticWorkItem] = []
        keys = sorted(by_entity)
        while keys:
            next_keys: list[str] = []
            for key in keys:
                bucket = by_entity[key]
                interleaved.append(bucket.popleft())
                if bucket:
                    next_keys.append(key)
            keys = next_keys

        batches: list[list[SemanticWorkItem]] = []
        current: list[SemanticWorkItem] = []
        current_bytes = 0
        for item in interleaved:
            size = len(
                json.dumps(
                    capsule_semantic_payload(item.capsule, item.digest),
                    ensure_ascii=False,
                    separators=(",", ":"),
                ).encode()
            )
            if current and (
                len(current) >= self.max_items or current_bytes + size > self.max_input_bytes
            ):
                batches.append(current)
                current, current_bytes = [], 0
            current.append(item)
            current_bytes += size
        if current:
            batches.append(current)
        return batches
