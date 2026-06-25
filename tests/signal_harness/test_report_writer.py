from __future__ import annotations

import json
from datetime import datetime, timezone

from signal_harness.signal.schemas import (
    SignalAssessment,
    SignalCategory,
    SignalDecision,
    SignalEvent,
    SourceQuality,
    TraceStep,
)
from signal_harness.tools.report_writer import write_json_outputs, write_radar_digest


def _fixtures():
    event = SignalEvent(
        event_id="demo-001",
        source_type="github_release",
        source_name="example/repo",
        title="Checkpoint release",
        content="Migration support",
        url="https://example.com/release",
        collected_at=datetime.now(timezone.utc),
    )
    assessment = SignalAssessment(
        event_id=event.event_id,
        category=SignalCategory.DEPENDENCY_UPDATE,
        relevance_score=90,
        impact_score=88,
        confidence=0.9,
        evidence_urls=[event.url],
        source_quality=SourceQuality.OFFICIAL,
        reason="Impacts checkpoint persistence.",
        action_items=["Review migration notes."],
        decision=SignalDecision.ACTION_REQUIRED,
    )
    trace = TraceStep(step="classify", status="success", duration_ms=1)
    return event, assessment, trace


def test_report_writer_generates_digest(tmp_path) -> None:
    event, assessment, _ = _fixtures()

    path = write_radar_digest(tmp_path, [event], [assessment])

    assert path.exists()
    assert "SignalHarness Radar Digest" in path.read_text(encoding="utf-8")
    assert "Checkpoint release" in path.read_text(encoding="utf-8")


def test_report_writer_generates_json_outputs(tmp_path) -> None:
    event, assessment, trace = _fixtures()

    paths = write_json_outputs(
        tmp_path,
        [event],
        [assessment],
        [{"event_id": event.event_id, "items": assessment.action_items}],
        [trace],
    )

    assert json.loads(paths["signals"].read_text(encoding="utf-8"))[0]["event_id"] == "demo-001"
    assert json.loads(paths["task_trace"].read_text(encoding="utf-8"))[0]["step"] == "classify"
