"""Evidence quality verification agent."""

from __future__ import annotations

from signal_harness.agents.models import EvidenceResult
from signal_harness.signal.schemas import SignalEvent, SourceQuality


class EvidenceAgent:
    """Assess provenance without inventing unsupported evidence."""

    name = "EvidenceAgent"

    def run(self, event: SignalEvent) -> EvidenceResult:
        official = bool(event.raw_payload.get("official"))
        if official or event.source_type in {"github_release", "github_issue"}:
            quality = SourceQuality.OFFICIAL
            confidence = 0.92 if event.url else 0.78
            reason = "The event points to an official repository source."
        elif event.source_type == "rss":
            quality = SourceQuality.SECONDARY
            confidence = 0.72 if event.url else 0.58
            reason = "The event comes from a tracked authored feed."
        elif event.source_type == "web_change":
            quality = SourceQuality.COMMUNITY
            confidence = 0.62 if event.url else 0.45
            reason = "The event is a direct observation but may lack explanatory context."
        else:
            quality = SourceQuality.UNVERIFIED
            confidence = 0.35
            reason = "No authoritative provenance could be established."
        return EvidenceResult(
            evidence_urls=[event.url] if event.url else [],
            source_quality=quality,
            confidence=confidence,
            reason=reason,
        )
