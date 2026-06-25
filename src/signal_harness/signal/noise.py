"""Conservative pre-LLM noise hints."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from signal_harness.signal.deduplicator import signal_fingerprint
from signal_harness.signal.schemas import (
    FeedbackLabel,
    FeedbackRecord,
    NoiseAssessment,
    SignalEvent,
)

DEFAULT_SOURCE_TYPES = frozenset(
    {"github_release", "github_issue", "rss", "web_change", "team_update"}
)
LOW_INFORMATION_TERMS = (
    "footer",
    "typo",
    "formatting only",
    "wording update",
    "whitespace",
    "copyright",
)


class NoiseFilter:
    """Route or downweight obvious noise without deleting source records."""

    def evaluate(
        self,
        events: Iterable[SignalEvent],
        *,
        policy: dict[str, Any],
        feedback_history: Iterable[FeedbackRecord] = (),
        seen_hashes: set[str] | None = None,
    ) -> list[NoiseAssessment]:
        negative_patterns = [
            record.note.strip().lower()
            for record in feedback_history
            if record.feedback
            in {
                FeedbackLabel.FALSE_POSITIVE,
                FeedbackLabel.NOT_USEFUL,
                FeedbackLabel.TOO_GENERIC,
            }
            and record.note.strip()
        ]
        ignore_patterns = [
            str(item).strip().lower()
            for item in policy.get("ignore_patterns", [])
            if str(item).strip()
        ]
        seen = seen_hashes or set()
        assessments: list[NoiseAssessment] = []
        for event in events:
            text = f"{event.title} {event.content}".lower()
            rules: list[str] = []
            multiplier = 1.0
            strong_noise = False
            if signal_fingerprint(event) in seen:
                rules.append("duplicate_hash")
                # Novelty is already penalized by the deterministic scorer.
                # Keep this as a light routing hint instead of double-punishing
                # a recurring but still relevant upstream signal.
                multiplier *= 0.85
            matched_ignore = [item for item in ignore_patterns if item in text]
            if matched_ignore:
                rules.append("ignore_pattern:" + ",".join(matched_ignore))
                multiplier *= 0.35
                strong_noise = True
            if event.source_type == "web_change" and any(
                term in text for term in LOW_INFORMATION_TERMS
            ):
                rules.append("low_information_web_change")
                multiplier *= 0.2
                strong_noise = True
            matched_feedback = [item for item in negative_patterns if item in text]
            if matched_feedback:
                rules.append("known_false_positive_pattern")
                multiplier *= 0.5
            if event.source_type not in DEFAULT_SOURCE_TYPES:
                rules.append("source_type_not_allowlisted")
                multiplier *= 0.25
                strong_noise = True
            missing = [
                field
                for field, value in (
                    ("title", event.title),
                    ("content", event.content),
                    ("url", event.url),
                )
                if not value.strip()
            ]
            if len(missing) >= 2:
                rules.append("low_quality_missing:" + ",".join(missing))
                multiplier *= 0.5
            assessments.append(
                NoiseAssessment(
                    event_id=event.event_id,
                    is_noise_candidate=strong_noise,
                    score_multiplier=round(max(0.1, multiplier), 3),
                    noise_reason="; ".join(rules) if rules else None,
                    matched_rules=rules,
                )
            )
        return assessments
