from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path

from signal_harness.agent_integration.mode import RunMode
from signal_harness.alerts import AlertPolicy, write_alert_outputs
from signal_harness.providers.mock_provider import MockProvider
from signal_harness.runtime.workflow import SignalHarnessWorkflow
from signal_harness.signal.schemas import (
    SignalAssessment,
    SignalCategory,
    SignalDecision,
    SignalEvent,
    SourceQuality,
)
from signal_harness.ui.dashboard import write_dashboard
from signal_harness.ui.digest import write_digest


def _event(event_id: str = "alert-001") -> SignalEvent:
    now = datetime(2026, 6, 29, tzinfo=timezone.utc)
    return SignalEvent(
        event_id=event_id,
        source_type="github_release",
        source_name="example/project",
        title="Security-sensitive provider release",
        content="A provider permission release changes core tool handling.",
        url="https://example.test/release",
        published_at=now,
        raw_payload={"official": True},
        collected_at=now,
    )


def _assessment(event_id: str = "alert-001") -> SignalAssessment:
    return SignalAssessment(
        event_id=event_id,
        category=SignalCategory.DEPENDENCY_UPDATE,
        relevance_score=90,
        impact_score=92,
        confidence=0.88,
        affected_modules=["core-provider-permission"],
        evidence_urls=["https://example.test/release"],
        source_quality=SourceQuality.OFFICIAL,
        reason="Major provider release.",
        action_items=["Review provider permission handling."],
        decision=SignalDecision.ACTION_REQUIRED,
        cross_source_confidence=0.8,
    )


def test_alert_outputs_and_state_deduplicate(tmp_path: Path) -> None:
    output_dir = tmp_path / "outputs"
    state_dir = tmp_path / "state"

    write_alert_outputs(
        output_dir=output_dir,
        state_dir=state_dir,
        events=[_event()],
        assessments=[_assessment()],
        policy=AlertPolicy(alert_threshold=75),
    )

    alerts = json.loads((output_dir / "alerts.json").read_text(encoding="utf-8"))
    state = json.loads((state_dir / "alert_state.json").read_text(encoding="utf-8"))
    assert len(alerts) == 1
    assert "alert-001" in state["alerted_event_ids"]
    assert "Security-sensitive provider release" in (
        output_dir / "alerts.md"
    ).read_text(encoding="utf-8")

    write_alert_outputs(
        output_dir=output_dir,
        state_dir=state_dir,
        events=[_event()],
        assessments=[_assessment()],
        policy=AlertPolicy(alert_threshold=75),
    )

    assert json.loads((output_dir / "alerts.json").read_text(encoding="utf-8")) == []
    state = json.loads((state_dir / "alert_state.json").read_text(encoding="utf-8"))
    assert state["alerted_event_ids"].count("alert-001") == 1


def test_dashboard_and_digest_outputs_include_expected_sections(tmp_path: Path) -> None:
    output_dir = tmp_path / "outputs"
    output_dir.mkdir()
    (output_dir / "signals.json").write_text(
        json.dumps([_event().model_dump(mode="json")]),
        encoding="utf-8",
    )
    (output_dir / "impact_scores.json").write_text(
        json.dumps([_assessment().model_dump(mode="json")]),
        encoding="utf-8",
    )
    (output_dir / "task_trace.json").write_text(
        json.dumps(
            [
                {
                    "step": "llm_agent_call",
                    "status": "success",
                    "duration_ms": 10,
                    "agent_name": "ContextEvidenceAgent",
                    "tools_requested": ["github_signal"],
                    "tools_executed": ["github_signal"],
                    "blocked_tools": [],
                    "source_tasks": [
                        {
                            "task_id": "source-1",
                            "source_name": "example/project",
                            "source_type": "github_release",
                            "status": "success",
                            "duration_ms": 1,
                            "output_count": 1,
                            "cache_hit": False,
                        }
                    ],
                }
            ]
        ),
        encoding="utf-8",
    )
    (output_dir / "alerts.json").write_text(
        json.dumps(
            [
                {
                    "event_id": "alert-001",
                    "title": "Security-sensitive provider release",
                    "reasons": ["decision=action_required"],
                }
            ]
        ),
        encoding="utf-8",
    )
    (output_dir / "latest_learning_observation.json").write_text(
        json.dumps(
            {
                "learning_summary": "Keep provider permission signals visible.",
                "memory_sections_read": ["feedback_memory"],
            }
        ),
        encoding="utf-8",
    )

    dashboard = write_dashboard(output_dir)
    daily = write_digest(output_dir, period="daily")
    weekly = write_digest(output_dir, period="weekly")

    html = dashboard.read_text(encoding="utf-8")
    assert "SignalHarness Dashboard" in html
    assert "High priority signals" in html
    assert "Alerts" in html
    assert "Source health" in html
    assert "Agent trace and tools" in html
    assert "Learning proposal summary" in html
    for digest in (daily, weekly):
        text = digest.read_text(encoding="utf-8")
        assert "Major events" in text
        assert "Source health" in text
        assert "Learning proposal summary" in text


def test_scan_operational_outputs_and_learning_observation_modes(
    project_root: Path,
    tmp_path: Path,
) -> None:
    fixture = project_root / "examples/signal_harness/sample_events.json"
    expected_outputs = {
        "signals.json",
        "impact_scores.json",
        "action_items.json",
        "task_trace.json",
        "alerts.json",
        "alerts.md",
        "dashboard.html",
        "radar_digest.md",
        "run_summary.txt",
    }

    demo_output = tmp_path / "demo_outputs"
    demo_state = tmp_path / "demo_state"
    demo = SignalHarnessWorkflow(
        cwd=project_root,
        output_dir=demo_output,
        state_dir=demo_state,
        mode=RunMode.DEMO,
    )
    asyncio.run(demo.scan(fixture=fixture))

    assert expected_outputs <= {path.name for path in demo_output.iterdir()}
    assert not (demo_output / "latest_learning_observation.json").exists()
    assert not (demo_state / "latest_learning_observation.json").exists()

    mock_output = tmp_path / "mock_outputs"
    mock_state = tmp_path / "mock_state"
    mock = SignalHarnessWorkflow(
        cwd=project_root,
        output_dir=mock_output,
        state_dir=mock_state,
        mode=RunMode.MOCK_AGENT,
        provider=MockProvider(strategy="scripted"),
    )
    asyncio.run(mock.scan(fixture=fixture))

    assert expected_outputs <= {path.name for path in mock_output.iterdir()}
    assert (mock_output / "latest_learning_observation.json").exists()
    assert (mock_state / "latest_learning_observation.json").exists()
