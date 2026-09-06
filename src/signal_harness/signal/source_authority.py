"""Deterministic provenance authority for collected external signals."""

from __future__ import annotations

from signal_harness.signal.schemas import SignalEvent, SourceQuality

_MAINTAINER_ASSOCIATIONS = {"OWNER", "MEMBER", "COLLABORATOR"}
_QUALITY_RANK = {
    SourceQuality.UNVERIFIED: 0,
    SourceQuality.COMMUNITY: 1,
    SourceQuality.SECONDARY: 2,
    SourceQuality.MAINTAINER: 3,
    SourceQuality.OFFICIAL: 4,
}
_CONFIDENCE_CAP = {
    SourceQuality.UNVERIFIED: 0.45,
    SourceQuality.COMMUNITY: 0.65,
    SourceQuality.SECONDARY: 0.75,
    SourceQuality.MAINTAINER: 0.85,
    SourceQuality.OFFICIAL: 0.95,
}


def github_issue_authority(raw: dict[str, object]) -> str:
    explicit = str(raw.get("source_authority") or "").strip().lower()
    if explicit in {"official", "maintainer", "community"}:
        return explicit
    if raw.get("official") is True:
        return "official"
    association = str(raw.get("author_association") or "").strip().upper()
    if association in _MAINTAINER_ASSOCIATIONS:
        return "maintainer"
    return "community"


def event_source_quality(event: SignalEvent) -> SourceQuality:
    raw = event.raw_payload
    if event.source_type == "github_release":
        return SourceQuality.OFFICIAL if raw.get("official", True) else SourceQuality.COMMUNITY
    if event.source_type == "github_issue":
        authority = github_issue_authority(raw)
        if authority == "official":
            return SourceQuality.OFFICIAL
        if authority == "maintainer":
            return SourceQuality.MAINTAINER
        return SourceQuality.COMMUNITY
    if event.source_type == "rss":
        return SourceQuality.OFFICIAL if raw.get("official") is True else SourceQuality.SECONDARY
    if event.source_type == "web_change":
        return SourceQuality.OFFICIAL if raw.get("official") is True else SourceQuality.SECONDARY
    if event.source_type == "team_update":
        return SourceQuality.OFFICIAL
    return SourceQuality.UNVERIFIED


def clamp_source_quality(
    event: SignalEvent,
    claimed: SourceQuality,
    confidence: float,
) -> tuple[SourceQuality, float]:
    """Prevent an LLM from upgrading provenance beyond deterministic source evidence."""

    allowed = event_source_quality(event)
    quality = claimed if _QUALITY_RANK[claimed] <= _QUALITY_RANK[allowed] else allowed
    return quality, min(confidence, _CONFIDENCE_CAP[quality])
