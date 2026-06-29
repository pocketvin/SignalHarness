"""Deterministic in-batch and cross-run signal duplicate detection."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import TYPE_CHECKING, Iterable

from signal_harness.utils.fs import atomic_write_text
from signal_harness.signal.schemas import SignalEvent

if TYPE_CHECKING:
    from signal_harness.signal.schemas import SignalAssessment


def _string_values(value: object) -> set[str]:
    if not isinstance(value, list):
        return set()
    return {str(item) for item in value}


def signal_fingerprint(event: SignalEvent) -> str:
    """Return a stable content fingerprint for a normalized signal."""

    canonical = "|".join(
        (
            event.source_type.lower(),
            event.source_name.lower(),
            " ".join(event.title.lower().split()),
            event.url.lower().rstrip("/"),
        )
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def deduplicate_events(events: Iterable[SignalEvent]) -> tuple[list[SignalEvent], list[str]]:
    """Remove duplicate IDs or fingerprints while preserving source order."""

    unique: list[SignalEvent] = []
    duplicate_ids: list[str] = []
    seen_ids: set[str] = set()
    seen_hashes: set[str] = set()
    for event in events:
        fingerprint = signal_fingerprint(event)
        if event.event_id in seen_ids or fingerprint in seen_hashes:
            duplicate_ids.append(event.event_id)
            continue
        unique.append(event)
        seen_ids.add(event.event_id)
        seen_hashes.add(fingerprint)
    return unique, duplicate_ids


def load_seen_hashes(path: str | Path) -> set[str]:
    """Load cross-run fingerprints from a readable JSON memory file."""

    target = Path(path)
    if not target.exists():
        return set()
    payload = json.loads(target.read_text(encoding="utf-8"))
    values = payload.get("duplicate_hashes", []) if isinstance(payload, dict) else []
    return {str(value) for value in values}


def save_seen_signals(
    path: str | Path,
    events: Iterable[SignalEvent],
    assessments: Iterable[SignalAssessment] = (),
) -> Path:
    """Merge event IDs and fingerprints into signal memory."""

    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    existing: dict[str, object] = {}
    if target.exists():
        loaded = json.loads(target.read_text(encoding="utf-8"))
        if isinstance(loaded, dict):
            existing = loaded
    seen_signals = _string_values(existing.get("seen_signals"))
    duplicate_hashes = _string_values(existing.get("duplicate_hashes"))
    for event in events:
        seen_signals.add(event.event_id)
        duplicate_hashes.add(signal_fingerprint(event))
    payload = {
        "seen_signals": sorted(seen_signals),
        "duplicate_hashes": sorted(duplicate_hashes),
        "previous_assessments": [
            item.model_dump(mode="json") for item in assessments
        ]
        or existing.get("previous_assessments", []),
    }
    atomic_write_text(target, json.dumps(payload, indent=2, ensure_ascii=False) + "\n")
    return target
