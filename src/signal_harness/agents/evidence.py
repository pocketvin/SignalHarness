"""Evidence quality verification agent."""

from __future__ import annotations

from signal_harness.agents.models import EvidenceResult
from signal_harness.signal.schemas import SignalEvent, SourceQuality
from signal_harness.signal.source_authority import event_source_quality


class EvidenceAgent:
    """Assess provenance without inventing unsupported evidence."""

    name = "EvidenceAgent"

    def run(self, event: SignalEvent) -> EvidenceResult:
        quality = event_source_quality(event)
        if quality is SourceQuality.OFFICIAL:
            confidence = 0.92 if event.url else 0.78
            reason = "The event is backed by an official first-party source."
        elif quality is SourceQuality.MAINTAINER:
            confidence = 0.82 if event.url else 0.68
            reason = "The event was authored by a repository maintainer or collaborator."
        elif quality is SourceQuality.SECONDARY:
            confidence = 0.72 if event.url else 0.58
            reason = "The event comes from a tracked authored feed."
        elif quality is SourceQuality.COMMUNITY:
            confidence = 0.58 if event.url else 0.42
            reason = "The event is a community-originated observation, not an official statement."
        else:
            confidence = 0.35
            reason = "No authoritative provenance could be established."
        return EvidenceResult(
            evidence_urls=[event.url] if event.url else [],
            source_quality=quality,
            confidence=confidence,
            reason=reason,
        )
