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
    TraceStep,
)
from signal_harness.ui.dashboard import write_dashboard
from signal_harness.ui.digest import write_digest
from signal_harness.ui.trace_view import write_trace_summary


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
    (output_dir / "latest_learning_staging.json").write_text(
        json.dumps(
            {
                "proposals": [
                    {
                        "proposal_id": "proposal-1",
                        "status": "staged",
                        "risk": {
                            "risk_level": "low",
                            "replay_gate_passed": True,
                        },
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    (output_dir / "model_eval_summary.json").write_text(
        json.dumps(
            {
                "provider": "mock-provider",
                "model": "mock-model",
                "model_profile": "mock-agent",
                "run_state_mode": "shared",
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
    assert "Model, profile, and limits" in html
    assert "Agent trace and tools" in html
    assert "Agent repair pass" in html
    assert "No repair pass was triggered." in html
    assert "Score breakdown" in html
    assert "Learning proposal summary" in html
    assert "Learning staging" in html
    assert "proposal-1" in html
    for digest in (daily, weekly):
        text = digest.read_text(encoding="utf-8")
        assert "Major events" in text
        assert "Source health" in text
        assert "Learning proposal summary" in text


def test_dashboard_and_trace_summary_show_repair_pass(tmp_path: Path) -> None:
    output_dir = tmp_path / "outputs"
    output_dir.mkdir()
    (output_dir / "signals.json").write_text("[]", encoding="utf-8")
    (output_dir / "impact_scores.json").write_text("[]", encoding="utf-8")
    trace_payload = [
        {
            "step": "repair_requested",
            "status": "success",
            "duration_ms": 0,
            "agent": "RepairCoordinator",
            "input_count": 1,
            "output_count": 1,
            "detail": (
                "triggered_by=ImpactAnalystAgent; "
                "target_agent=context_evidence; event_ids=demo-001; reason=test"
            ),
        },
        {
            "step": "repair_context_evidence",
            "status": "success",
            "duration_ms": 0,
            "agent": "RepairCoordinator",
            "input_count": 1,
            "output_count": 1,
            "detail": "Executed bounded Impact→ContextEvidence repair; event_ids=demo-001",
        },
        {
            "step": "repair_impact",
            "status": "success",
            "duration_ms": 0,
            "agent": "RepairCoordinator",
            "input_count": 1,
            "output_count": 1,
            "detail": "Reran ImpactAnalystAgent after evidence repair; event_ids=demo-001",
        },
        {
            "step": "repair_action",
            "status": "success",
            "duration_ms": 0,
            "agent": "RepairCoordinator",
            "input_count": 1,
            "output_count": 1,
            "detail": "Reran ActionPlannerAgent after impact repair; event_ids=demo-001",
        },
        {
            "step": "repair_blocked",
            "status": "success",
            "duration_ms": 0,
            "agent": "RepairCoordinator",
            "input_count": 1,
            "output_count": 0,
            "fallback_used": True,
            "detail": (
                "triggered_by=ActionPlannerAgent; target_agent=impact; "
                "event_ids=demo-002; reason=repair_round_budget_exceeded"
            ),
        },
    ]
    (output_dir / "task_trace.json").write_text(
        json.dumps(trace_payload),
        encoding="utf-8",
    )
    (output_dir / "alerts.json").write_text("[]", encoding="utf-8")

    dashboard = write_dashboard(output_dir)
    summary = write_trace_summary(
        output_dir,
        [TraceStep.model_validate(item) for item in trace_payload],
    )

    html = dashboard.read_text(encoding="utf-8")
    text = summary.read_text(encoding="utf-8")
    assert "Agent repair pass" in html
    assert "repair_requested" in html
    assert "repair_context_evidence" in html
    assert "repair_impact" in html
    assert "repair_action" in html
    assert "repair_blocked" in html
    assert "Impact→ContextEvidence" in html
    assert "## Agent Repair Pass" in text
    assert "- requested: 1" in text
    assert "- executed: 3" in text
    assert "- blocked: 1" in text
    assert "- fallback: 1" in text
    assert "- event_ids: demo-001, demo-002" in text


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
