"""Deterministic source assembly, compact context, and guarded product projections."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime
from typing import Any
from urllib.parse import urlsplit

from signal_harness.intelligence.contracts import (
    ChangeDigest,
    Direction,
    DirectionCandidate,
    Evidence,
    ProductChange,
    ShallowInsight,
)
from signal_harness.signal.schemas import SignalEvent
from signal_harness.signal.source_identity import release_package_identity
from signal_harness.tools.web_snapshot import normalize_web_text


def identity(prefix: str, value: Any) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str).encode()
    return prefix + hashlib.sha256(raw).hexdigest()[:24]


def source_revision_events(events: list[SignalEvent]) -> list[SignalEvent]:
    """Collapse identical source observations only; never discard changed content by title."""
    result: dict[str, SignalEvent] = {}
    for event in events:
        # The legacy fingerprint excludes content; the product path must not do that.
        key = identity("", (event.source_type, event.source_name, event.event_id, event.url))
        previous = result.get(key)
        if previous is None or event.collected_at >= previous.collected_at:
            result[key] = event
    return list(result.values())


def assemble_changes(
    events: list[SignalEvent],
    mapping: dict[str, tuple[str, int]],
) -> list[ChangeDigest]:
    """Reuse ledger canonical identity. Similar topics NEVER imply a merge."""
    grouped: dict[str, list[tuple[SignalEvent, int]]] = {}
    for event in events:
        change_id, revision = mapping[event.event_id]
        grouped.setdefault(change_id, []).append((event, revision))
    result: list[ChangeDigest] = []
    for change_id, rows in sorted(grouped.items()):
        evidence: list[Evidence] = []
        for event, revision in sorted(rows, key=lambda row: row[1]):
            text = normalize_web_text(event.content or event.title)
            authority = str(event.raw_payload.get("source_authority") or "unverified")
            evidence.append(
                Evidence(
                    evidence_id=f"evr-{revision}",
                    event_revision_id=revision,
                    source_name=event.source_name,
                    source_type=event.source_type,
                    url=event.url,
                    authority=authority,
                    excerpt=text[:1800],
                    excerpt_truncated=len(text) > 1800,
                )
            )
        # Prefer original/maintainer evidence for presentation, without throwing other sources away.
        primary, _ = min(
            rows,
            key=lambda row: (
                str(row[0].raw_payload.get("source_authority")) not in {"official", "maintainer"},
                row[1],
            ),
        )
        package = release_package_identity(primary)
        result.append(
            ChangeDigest(
                change_id=change_id,
                revision_id=identity("cr-", [change_id, sorted({r for _, r in rows})]),
                title=primary.title,
                entity=package.name if package else primary.source_name,
                kind=primary.source_type,
                published_at=primary.published_at.isoformat() if primary.published_at else None,
                current_version=primary.current_version,
                evidence=evidence,
            )
        )
    return sorted(result, key=lambda item: (item.published_at or "", item.change_id))


def compact_digest(item: ChangeDigest) -> dict[str, Any]:
    """Every source retains identity; only evidence excerpts are explicitly bounded."""
    return item.model_dump(mode="json")


def matching_preferences(
    digest: ChangeDigest, insight: ShallowInsight, profile: dict[str, Any]
) -> list[str]:
    entity = digest.entity.casefold()
    sources = {e.source_name.casefold() for e in digest.evidence}
    topics = {t.casefold() for t in insight.topics}
    text = f"{digest.title} {insight.what_changed}".casefold()
    matches: list[str] = []
    for pref in profile.get("importance_preferences", []):
        scope = str(pref.get("scope_type", ""))
        key = str(pref.get("scope_key", "")).casefold().strip()
        if not key:
            continue
        if scope == "source":
            match = key in sources
        elif scope == "category":
            match = key == digest.kind.casefold()
        else:
            match = (
                key == entity
                or key in topics
                or bool(re.search(r"(?<![\w])" + re.escape(key) + r"(?![\w])", text))
            )
        if match:
            matches.append(str(pref.get("importance", "normal")))
    return matches


def project_change(
    digest: ChangeDigest,
    insight: ShallowInsight | None,
    profile: dict[str, Any],
) -> ProductChange:
    if insight is None:
        return ProductChange(
            **digest.model_dump(exclude={"current_version"}),
            summary=digest.title,
            what_changed="本条变化的自动解释暂未完成，可查看原始证据。",
            project_relation="unknown",
            relation_reason="尚未判断与项目的关系。",
            attention="normal",
            topics=[],
            uncertainty="解释缺失，不代表没有影响。",
            interpretation_status="unavailable",
            relevant=False,
        )
    valid_ids = {item.evidence_id for item in digest.evidence}
    if not insight.evidence_ids or not set(insight.evidence_ids) <= valid_ids:
        raise ValueError("Insight evidence does not belong to its ChangeRevision")
    preferences = matching_preferences(digest, insight, profile)
    ignored = "ignore" in preferences
    explicit_interest = bool({"critical", "important"} & set(preferences))
    return ProductChange(
        **digest.model_dump(exclude={"current_version"}),
        **insight.model_dump(exclude={"change_id", "evidence_ids", "attention"}),
        attention="low"
        if ignored or "low" in preferences
        else "watch"
        if explicit_interest
        else insight.attention,
        relevant=not ignored
        and (explicit_interest or insight.project_relation in {"direct", "context"}),
        interpretation_status="ready",
    )


def corpus_payload(changes: list[ProductChange]) -> list[dict[str, Any]]:
    """Compact ALL changes, including weak/context-only/failed interpretations."""
    return [
        {
            "change_id": item.change_id,
            "revision_id": item.revision_id,
            "entity": item.entity,
            "kind": item.kind,
            "published_at": item.published_at,
            "summary": item.summary,
            "what_changed": item.what_changed,
            "topics": item.topics,
            "project_relation": item.project_relation,
            "relation_reason": item.relation_reason,
            "uncertainty": item.uncertainty,
            "interpretation_status": item.interpretation_status,
            "evidence": [
                e.model_dump(exclude={"excerpt"}) | {"excerpt": e.excerpt[:420]}
                for e in item.evidence
            ],
        }
        for item in changes
    ]


def finalize_direction(
    candidate: DirectionCandidate,
    *,
    changes: dict[str, ProductChange],
    project_id: str,
    scan_id: str,
    previous: dict[str, Any] | None,
    window: dict[str, Any],
    coverage: str,
    sources: list[dict[str, Any]] | None = None,
) -> Direction:
    supports = list(dict.fromkeys(candidate.supporting_change_ids))
    contradictions = list(dict.fromkeys(candidate.contradicting_change_ids))
    if len(supports) < 2 or not set(supports + contradictions) <= changes.keys():
        raise ValueError("Direction must cite at least two distinct existing Changes")
    if set(supports) & set(contradictions):
        raise ValueError("Direction support and contradiction sets overlap")
    prior: dict[str, Any] | None = None
    for item in (previous or {}).get("directions", []):
        if (
            candidate.previous_direction_id == item["direction_id"]
            or candidate.topic_key == item["topic_key"]
        ):
            prior = item
            break
    if candidate.previous_direction_id and prior is None:
        raise ValueError("Unknown previous direction identity")
    direction_id = (
        prior["direction_id"]
        if prior
        else identity("dir-", [project_id, candidate.topic_key.casefold()])
    )
    state: Any = "new" if prior is None else "continuing"
    reason = (
        "首次在已保存的报告中观察到，尚不足以判断长期趋势。"
        if prior is None
        else "与此前方向有关；窗口或来源覆盖不同，暂不比较增减。"
    )
    old_count = len(prior["supporting_change_ids"]) if prior else None
    if prior and previous and coverage == previous.get("coverage_status") == "complete":
        old_window = previous.get("window", {})
        try:
            lower = datetime.fromisoformat(str(window["from"]))
            upper = datetime.fromisoformat(str(window["to"]))
            old_lower = datetime.fromisoformat(str(old_window["from"]))
            old_upper = datetime.fromisoformat(str(old_window["to"]))
            hours = (upper - lower).total_seconds() / 3600
            old_hours = (old_upper - old_lower).total_seconds() / 3600
            comparable = (
                old_upper <= lower
                and hours > 0
                and old_hours > 0
                and 0.5 <= hours / old_hours <= 2
                and sources is not None
                and {(v.get("source_name"), v.get("source_type")) for v in sources}
                == {
                    (v.get("source_name"), v.get("source_type"))
                    for v in previous.get("sources", [])
                }
                and not (set(supports) & set(prior["supporting_change_ids"]))
            )
            if comparable:
                rate = len(supports) / hours
                old_rate = len(prior["supporting_change_ids"]) / old_hours
                state = (
                    "strengthening"
                    if rate > old_rate * 1.3
                    else "weakening"
                    if rate < old_rate * 0.7
                    else "continuing"
                )
                reason = "按不重叠窗口内的独立变化数量与时长比较；不是转发量或市场情绪。"
        except (KeyError, ValueError, TypeError):
            pass
    if (
        contradictions
        or coverage != "complete"
        or any(changes[c].interpretation_status != "ready" for c in supports)
    ):
        state, reason = "uncertain", "存在反向证据、解释缺失或来源覆盖缺口，暂不判定增强或减弱。"
    source_domains = {
        urlsplit(e.url).hostname or e.source_name
        for change_id in supports
        for e in changes[change_id].evidence
        if e.authority in {"official", "maintainer"}
    }
    return Direction(
        direction_id=direction_id,
        revision_id=identity("dr-", [scan_id, direction_id, supports]),
        topic_key=candidate.topic_key,
        title=candidate.title,
        explanation=candidate.explanation,
        state=state,
        state_reason=reason,
        supporting_change_ids=supports,
        contradicting_change_ids=contradictions,
        independent_source_count=len(source_domains),
        previous_support_count=old_count,
        project_connection=candidate.project_connection,
        watch_next=candidate.watch_next,
        uncertainty=candidate.uncertainty,
    )
