"""Ultra-compact full-corpus representation for the one strong synthesis call."""

from __future__ import annotations

from collections import Counter
from typing import Any

from signal_harness.intelligence.contracts import ProductChange

DIGEST_FIELDS = {
    "id": "change_id",
    "e": "entity",
    "k": "change kind",
    "d": "published date (day precision)",
    "p": "evidence posture: reported/mixed/observed",
    "f": "compact world fact",
    "t": "topics",
    "r": "project relation: direct/context/none/unknown",
    "o": "origin: watched/discovered",
    "b": "project-conditioned discovery basis when discovered",
    "s": "up to three authority:source identities",
    "st": "interpretation status when unavailable",
}

_TABLE_FIELDS = ("e", "k", "d", "p", "t", "r", "o", "b", "s", "st")


def _posture(item: ProductChange) -> str:
    kinds = {e.source_type for e in item.evidence}
    if kinds == {"github_issue"}:
        return "reported"
    if "github_issue" in kinds:
        return "mixed"
    return "observed"


def direction_digest(
    item: ProductChange,
    *,
    fact_chars: int = 88,
    include_topics: bool = True,
) -> dict[str, Any]:
    """One compact row; full evidence remains in storage and Deep Dive, not in global context."""
    sources = list(dict.fromkeys(f"{e.authority}:{e.source_name}" for e in item.evidence))[:3]
    fact = item.what_changed if item.interpretation_status == "ready" else item.title
    payload: dict[str, Any] = {
        "id": item.change_id,
        "e": item.entity,
        "k": item.kind,
        "d": item.published_at[:10] if item.published_at else None,
        "p": _posture(item),
        "f": fact[:fact_chars],
        "r": item.project_relation,
        "o": item.discovery_origin,
        "b": item.discovery_basis[:160] if item.discovery_basis else "",
        "s": sources,
    }
    if include_topics and item.topics:
        payload["t"] = item.topics[:3]
    if item.interpretation_status != "ready":
        payload["st"] = "unavailable"
    return payload


def _pack_digests(digests: list[dict[str, Any]]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Dictionary-encode repeated categorical strings without dropping any Change.

    The previous compact rows still repeated repository names, topics, source identities,
    dates, posture and relation strings hundreds of times. The global synthesizer needs the
    complete corpus, but it does not need those repeated literals. Every Change keeps its
    literal ``id`` and compact fact while categorical values become zero-based indexes into
    deterministic tables carried in ``corpus_legend``.
    """

    values: dict[str, list[str]] = {field: [] for field in _TABLE_FIELDS}
    for field in _TABLE_FIELDS:
        observed: set[str] = set()
        for item in digests:
            raw = item.get(field)
            rows = raw if isinstance(raw, list) else [raw]
            for value in rows:
                if value is None or value == "":
                    continue
                observed.add(str(value))
        values[field] = sorted(observed, key=lambda value: (value.casefold(), value))
    indexes = {
        field: {value: index for index, value in enumerate(table)}
        for field, table in values.items()
    }

    packed: list[dict[str, Any]] = []
    for item in digests:
        row: dict[str, Any] = {"id": item["id"], "f": item["f"]}
        for field in ("e", "k", "d", "p", "r", "o", "b", "st"):
            raw = item.get(field)
            if raw is None or raw == "":
                continue
            row[field] = indexes[field][str(raw)]
        for field in ("t", "s"):
            raw_values = item.get(field)
            if not isinstance(raw_values, list) or not raw_values:
                continue
            row[field] = [indexes[field][str(value)] for value in raw_values]
        packed.append(row)

    legend = {
        "format": (
            "id and f are literal strings; e/k/d/p/t/r/o/b/s/st are zero-based indexes "
            "into the tables below"
        ),
        "fields": DIGEST_FIELDS,
        "tables": values,
    }
    return legend, packed


def organize_direction_corpus(
    changes: list[ProductChange], *, fact_chars: int = 88
) -> dict[str, Any]:
    """All Changes remain present; repeated strings are dictionary-encoded, never Top-K'd."""
    # Free-form shallow topics duplicate the compact fact and can create more unique labels than
    # Changes in an issue-heavy scan. Keep them in persisted ChangeInsight and project activity,
    # but omit them from the one global synthesis wire representation.
    digests = [
        direction_digest(item, fact_chars=fact_chars, include_topics=False) for item in changes
    ]
    digests.sort(
        key=lambda item: (
            (item.get("t") or ["~"])[0],
            str(item["e"]).casefold(),
            item.get("d") or "",
            item["id"],
        )
    )
    topic_counts = Counter(topic for item in digests for topic in item.get("t", []))
    entity_counts = Counter(str(item["e"]) for item in digests)
    posture_names = {"reported": "reported_issue", "mixed": "mixed", "observed": "observed_change"}
    posture_counts = Counter(posture_names[str(item["p"])] for item in digests)
    legend, packed = _pack_digests(digests)
    return {
        "legend": legend,
        "index": {
            "count": len(digests),
            "kind_counts": dict(Counter(str(item["k"]) for item in digests).most_common()),
            "posture_counts": dict(posture_counts.most_common()),
            "top_topics": dict(topic_counts.most_common(24)),
            "top_entities": dict(entity_counts.most_common(24)),
        },
        "items": packed,
    }


def organize_project_activity_context(
    changes: list[ProductChange],
    *,
    max_items: int = 24,
    fact_chars: int = 64,
) -> dict[str, Any]:
    """Keep full project-activity counts but only bounded representative rows for synthesis.

    Project activity is contextual background, never evidence for EnvironmentDirections. High-velocity
    repositories may produce hundreds of commits/PRs in one window; sending every row wastes strong
    model context without improving external-environment coverage. Representatives are selected
    deterministically, balanced across change kinds, and newest-first within each kind.
    """
    items = [direction_digest(item, fact_chars=fact_chars) for item in changes]
    items.sort(
        key=lambda item: (
            (item.get("t") or ["~"])[0],
            str(item["e"]).casefold(),
            item.get("d") or "",
            item["id"],
        )
    )
    topic_counts = Counter(topic for item in items for topic in item.get("t", []))
    entity_counts = Counter(str(item["e"]) for item in items)
    posture_names = {
        "reported": "reported_issue",
        "mixed": "mixed",
        "observed": "observed_change",
    }
    posture_counts = Counter(posture_names[str(item["p"])] for item in items)
    groups: dict[str, list[dict[str, Any]]] = {}
    for item in items:
        groups.setdefault(str(item["k"]), []).append(item)
    for rows in groups.values():
        rows.sort(
            key=lambda item: (item.get("d") or "", str(item["id"])),
            reverse=True,
        )
    representatives: list[dict[str, Any]] = []
    kinds = sorted(groups)
    while len(representatives) < max_items and any(groups[kind] for kind in kinds):
        for kind in kinds:
            if groups[kind]:
                representatives.append(groups[kind].pop(0))
                if len(representatives) >= max_items:
                    break
    return {
        "legend": DIGEST_FIELDS,
        "index": {
            "count": len(items),
            "kind_counts": dict(Counter(str(item["k"]) for item in items).most_common()),
            "posture_counts": dict(posture_counts.most_common()),
            "top_topics": dict(topic_counts.most_common(24)),
            "top_entities": dict(entity_counts.most_common(24)),
            "context_count": len(representatives),
            "context_truncated": len(items) > len(representatives),
        },
        "items": representatives,
    }
