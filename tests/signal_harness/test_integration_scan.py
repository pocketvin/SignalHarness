from __future__ import annotations

import asyncio
import json
import shutil
from datetime import datetime
from pathlib import Path

import yaml
from typer.testing import CliRunner

from signal_harness.cli import app
from signal_harness.agent_integration.mode import RunMode
from signal_harness.runtime.workflow import SignalHarnessWorkflow


runner = CliRunner()


def test_demo_mode_scan_does_not_write_learning_observation(
    project_root: Path,
    tmp_path: Path,
) -> None:
    output_dir = tmp_path / "outputs"
    state_dir = tmp_path / "state"
    fixture = project_root / "examples" / "signal_harness" / "sample_events.json"
    workflow = SignalHarnessWorkflow(
        cwd=project_root,
        output_dir=output_dir,
        state_dir=state_dir,
        mode=RunMode.DEMO,
    )

    result = asyncio.run(workflow.scan(fixture=fixture))

    assert result.assessments
    assert not (state_dir / "latest_learning_observation.json").exists()
    assert not (output_dir / "latest_learning_observation.json").exists()


def test_fixture_scan_feedback_and_calibration(project_root: Path, tmp_path: Path) -> None:
    output_dir = tmp_path / "outputs"
    state_dir = tmp_path / "state"
    fixture = project_root / "examples" / "signal_harness" / "sample_events.json"

    scan_result = runner.invoke(
        app,
        [
            "scan",
            "--fixture",
            str(fixture),
            "--cwd",
            str(project_root),
            "--output-dir",
            str(output_dir),
            "--state-dir",
            str(state_dir),
        ],
    )
    assert scan_result.exit_code == 0, scan_result.output
    for name in (
        "signals.json",
        "impact_scores.json",
        "action_items.json",
        "task_trace.json",
        "alerts.json",
        "alerts.md",
        "dashboard.html",
        "radar_digest.md",
        "run_summary.txt",
    ):
        assert (output_dir / name).exists(), name
    project_state = state_dir / "projects" / "signalharness"
    assert (project_state / "alert_state.json").exists()

    assessments = json.loads((output_dir / "impact_scores.json").read_text(encoding="utf-8"))
    assert any(item["decision"] in {"save", "alert", "action_required"} for item in assessments)
    trace = json.loads((output_dir / "task_trace.json").read_text(encoding="utf-8"))
    assert any(item["agent"] == "ClassifierAgent" for item in trace)
    assert any(item["step"] == "write_json_outputs" for item in trace)

    feedback_result = runner.invoke(
        app,
        [
            "feedback",
            "--signal-id",
            "demo-001",
            "--label",
            "useful",
            "--note",
            "checkpoint related signal is important",
            "--cwd",
            str(project_root),
            "--output-dir",
            str(output_dir),
            "--state-dir",
            str(state_dir),
        ],
    )
    assert feedback_result.exit_code == 0, feedback_result.output
    assert (project_state / "feedback_memory.json").exists()
    assert (project_state / "signal_policy_update_proposal.json").exists()
    signal_memory = json.loads((project_state / "signal_memory.json").read_text(encoding="utf-8"))
    assert signal_memory["previous_assessments"]

    calibrate_result = runner.invoke(
        app,
        [
            "calibrate",
            "--cwd",
            str(project_root),
            "--state-dir",
            str(state_dir),
        ],
    )
    assert calibrate_result.exit_code == 0, calibrate_result.output
    assert "Review the proposal" in calibrate_result.output

    config_copy = tmp_path / "configs"
    shutil.copytree(project_root / "configs", config_copy)
    staged_result = runner.invoke(
        app,
        [
            "calibrate",
            "--apply",
            "--cwd",
            str(project_root),
            "--config-dir",
            str(config_copy),
            "--state-dir",
            str(state_dir),
        ],
    )
    assert staged_result.exit_code == 0, staged_result.output
    assert "Staged proposal" in staged_result.output
    assert "Policy, watchlist, and skill changes were not applied." in staged_result.output
    staged_policy = yaml.safe_load((config_copy / "signal_policy.yaml").read_text(encoding="utf-8"))
    assert "checkpoint" not in staged_policy["suggested_focus_keywords"]
    assert (project_state / "learning_staging.json").exists()

    apply_result = runner.invoke(
        app,
        [
            "calibrate",
            "--apply",
            "--yes",
            "--cwd",
            str(project_root),
            "--config-dir",
            str(config_copy),
            "--state-dir",
            str(state_dir),
        ],
    )
    assert apply_result.exit_code != 0
    assert "insufficient_evidence" in apply_result.output
    unchanged = yaml.safe_load(
        (config_copy / "signal_policy.yaml").read_text(encoding="utf-8")
    )
    assert "checkpoint" not in unchanged["suggested_focus_keywords"]
    durable_replay = json.loads(
        (project_state / "calibration_replay.json").read_text(encoding="utf-8")
    )
    assert durable_replay["promotion_allowed"] is False
    assert durable_replay["recommendation"] == "insufficient_evidence"


def test_scan_applies_since_after_normalization_for_all_sources(
    project_root: Path,
    tmp_path: Path,
    monkeypatch,
) -> None:
    fixture_events = [
        {
            "event_id": "old-rss",
            "source_type": "rss",
            "source_name": "Official Feed",
            "title": "Old article",
            "content": "Old provider article",
            "url": "https://example.com/old",
            "published_at": "2026-06-01T00:00:00Z",
            "collected_at": "2026-06-25T00:00:00Z",
        },
        {
            "event_id": "new-rss",
            "source_type": "rss",
            "source_name": "Official Feed",
            "title": "New article",
            "content": "New provider article",
            "url": "https://example.com/new",
            "published_at": "2026-06-24T00:00:00Z",
            "collected_at": "2026-06-25T00:00:00Z",
        },
    ]
    workflow = SignalHarnessWorkflow(
        cwd=project_root,
        output_dir=tmp_path / "outputs",
        state_dir=tmp_path / "state",
        mode=RunMode.DEMO,
    )

    async def fake_fixture(_fixture):
        return fixture_events

    monkeypatch.setattr(workflow, "_load_fixture", fake_fixture)
    result = asyncio.run(
        workflow.scan(
            fixture="fixture.json",
            since=datetime.fromisoformat("2026-06-20T00:00:00+00:00"),
        )
    )

    assert [event.event_id for event in result.signals] == ["new-rss"]
    step = next(item for item in result.trace.steps if item.step == "time_window_filter")
    assert step.input_count == 2
    assert step.output_count == 1
