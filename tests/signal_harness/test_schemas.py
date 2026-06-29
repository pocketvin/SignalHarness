from __future__ import annotations

from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from signal_harness.signal.schemas import (
    FeedbackLabel,
    FeedbackRecord,
    PolicyUpdateProposal,
    SignalAssessment,
    SignalCategory,
    SignalDecision,
    SignalEvent,
    TraceStep,
)


def test_signal_event_accepts_structured_source_data() -> None:
    event = SignalEvent(
        event_id="demo-001",
        source_type="github_release",
        source_name="langchain-ai/langgraph",
        title="Checkpoint persistence migration support",
        content="Release details",
        url="https://example.com/release",
        published_at="2026-06-20T09:00:00Z",
        raw_payload={"official": True},
        collected_at="2026-06-20T09:05:00Z",
    )

    assert event.event_id == "demo-001"
    assert event.published_at is not None
    assert event.raw_payload["official"] is True


def test_signal_assessment_enforces_score_ranges() -> None:
    with pytest.raises(ValidationError):
        SignalAssessment(
            event_id="demo-001",
            category=SignalCategory.DEPENDENCY_UPDATE,
            relevance_score=101,
            impact_score=80,
            confidence=0.9,
            decision=SignalDecision.ALERT,
        )


def test_feedback_record_uses_supported_labels() -> None:
    record = FeedbackRecord(
        event_id="demo-001",
        feedback=FeedbackLabel.USEFUL,
        note="Relevant to checkpoint persistence",
        created_at=datetime.now(timezone.utc),
    )

    assert record.feedback is FeedbackLabel.USEFUL


def test_policy_proposal_cannot_bypass_approval() -> None:
    with pytest.raises(ValidationError):
        PolicyUpdateProposal(
            proposal_id="proposal-001",
            reason="Repeated false positives",
            old_policy={"threshold": 75},
            new_policy={"threshold": 80},
            requires_approval=False,
        )


def test_trace_step_metadata_is_optional_and_structured() -> None:
    legacy = TraceStep(step="classify", status="success", duration_ms=1)
    structured = TraceStep(
        step="repair_requested",
        status="success",
        duration_ms=0,
        metadata={
            "repair": {
                "triggered_by": "ImpactAnalystAgent",
                "target_agent": "context_evidence",
                "event_ids": ["demo-001"],
            }
        },
    )

    assert legacy.metadata == {}
    assert structured.metadata["repair"]["event_ids"] == ["demo-001"]
