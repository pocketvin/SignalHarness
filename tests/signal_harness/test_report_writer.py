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
        reason="Deterministic fallback: Approval required before design review.",
        action_items=[
            "Review migration notes.",
            "Human approval is required before execution.",
        ],
        what_changed_zh="上游发布了会影响检查点持久化的迁移变更。",
        why_relevant_zh="当前项目依赖这条检查点路径，需要验证迁移兼容性。",
        action_items_zh=["先运行检查点迁移回归测试。"],
        report_summary_zh="本轮最需要关注的是检查点迁移兼容性。",
        decision=SignalDecision.ACTION_REQUIRED,
    )
    trace = TraceStep(step="classify", status="success", duration_ms=1)
    return event, assessment, trace


def test_report_writer_generates_digest(tmp_path) -> None:
    event, assessment, _ = _fixtures()

    path = write_radar_digest(tmp_path, [event], [assessment])

    assert path.exists()
    text = path.read_text(encoding="utf-8")
    assert "SignalHarness 项目环境雷达" in text
    assert "Checkpoint release" in text
    assert "本轮最需要关注的是检查点迁移兼容性。" in text
    assert "上游发布了会影响检查点持久化的迁移变更。" in text
    assert "当前项目依赖这条检查点路径，需要验证迁移兼容性。" in text
    assert "先运行检查点迁移回归测试。" in text
    assert "Deterministic fallback" not in text
    assert "Approval required before" not in text
    assert "Human approval" not in text
    assert "Reason:" not in text
    assert "Action items:" not in text


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
