"""Ultra-compact full-corpus representation for the one strong synthesis call."""

from __future__ import annotations

from collections import Counter
from typing import Any

from signal_harness.intelligence.contracts import ProductChange

DIGEST_LEGEND = {
    "id": "change_id",
    "e": "entity",
    "k": "change kind",
    "d": "published date (day precision)",
    "p": "evidence posture: reported/mixed/observed",
    "f": "compact world fact",
    "t": "topics",
    "r": "project relation: direct/context/none/unknown",
    "s": "up to three authority:source identities",
    "st": "interpretation status when unavailable",
}


def _posture(item: ProductChange) -> str:
    kinds = {e.source_type for e in item.evidence}
    if kinds == {"github_issue"}:
        return "reported"
    if "github_issue" in kinds:
        return "mixed"
    return "observed"


def direction_digest(item: ProductChange, *, fact_chars: int = 88) -> dict[str, Any]:
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
        "t": item.topics[:3],
        "r": item.project_relation,
        "s": sources,
    }
    if item.interpretation_status != "ready":
        payload["st"] = "unavailable"
    return payload


def organize_direction_corpus(
    changes: list[ProductChange], *, fact_chars: int = 88
) -> dict[str, Any]:
    """All Changes remain present; indexes contain counts only and never replace the corpus."""
    digests = [direction_digest(item, fact_chars=fact_chars) for item in changes]
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
    return {
        "legend": DIGEST_LEGEND,
        "index": {
            "count": len(digests),
            "kind_counts": dict(Counter(str(item["k"]) for item in digests).most_common()),
            "posture_counts": dict(posture_counts.most_common()),
            "top_topics": dict(topic_counts.most_common(24)),
            "top_entities": dict(entity_counts.most_common(24)),
        },
        "items": digests,
    }
