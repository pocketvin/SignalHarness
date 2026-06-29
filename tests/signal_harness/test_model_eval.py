from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from signal_harness.cli import app
from signal_harness.evals import build_model_eval_summary
from signal_harness.signal.schemas import TraceStep

runner = CliRunner()


def test_model_eval_mock_agent_writes_summary(
    project_root: Path,
    tmp_path: Path,
) -> None:
    result = runner.invoke(
        app,
        [
            "model-eval",
            "--fixture",
            str(project_root / "examples/signal_harness/sample_events.json"),
            "--mode",
            "mock-agent",
            "--cwd",
            str(project_root),
            "--output-dir",
            str(tmp_path / "outputs"),
            "--state-dir",
            str(tmp_path / "state"),
        ],
    )

    assert result.exit_code == 0, result.output
    summary_path = tmp_path / "outputs/model_eval_summary.json"
    markdown_path = tmp_path / "outputs/model_eval_summary.md"
    assert summary_path.exists()
    assert markdown_path.exists()
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    assert "schema_valid_rate" in summary
    assert "fallback_rate" in summary
    assert summary["provider"] == "mock-provider"
    assert summary["model"] == "mock-signal-model-v2"
    assert summary["run_state_mode"] == "shared"
    assert summary["isolated_state"] is False
    assert "repair_requested_count" in summary


def test_model_eval_agent_without_key_is_not_default_real_api(
    project_root: Path,
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.delenv("LLM_API_KEY", raising=False)

    result = runner.invoke(
        app,
        [
            "model-eval",
            "--fixture",
            str(project_root / "examples/signal_harness/sample_events.json"),
            "--mode",
            "agent",
            "--cwd",
            str(project_root),
            "--output-dir",
            str(tmp_path / "outputs"),
            "--state-dir",
            str(tmp_path / "state"),
        ],
    )

    assert result.exit_code == 2
    assert "agent mode requires LLM_API_KEY" in result.output


def test_model_eval_multiple_runs_use_isolated_state_dirs(
    project_root: Path,
    tmp_path: Path,
) -> None:
    result = runner.invoke(
        app,
        [
            "model-eval",
            "--fixture",
            str(project_root / "examples/signal_harness/sample_events.json"),
            "--mode",
            "mock-agent",
            "--runs",
            "2",
            "--cwd",
            str(project_root),
            "--output-dir",
            str(tmp_path / "outputs"),
            "--state-dir",
            str(tmp_path / "state"),
        ],
    )

    assert result.exit_code == 0, result.output
    assert (tmp_path / "state/run-001/signal_memory.json").exists()
    assert (tmp_path / "state/run-002/signal_memory.json").exists()
    summary = json.loads(
        (tmp_path / "outputs/model_eval_summary.json").read_text(encoding="utf-8")
    )
    assert summary["runs"] == 2
    assert summary["run_state_mode"] == "isolated_per_run"
    assert summary["isolated_state"] is True


def test_model_eval_summary_counts_repair_metrics() -> None:
    summary = build_model_eval_summary(
        assessments=[],
        trace=[
            TraceStep(
                step="repair_requested",
                status="success",
                duration_ms=0,
                detail="event_ids=demo-001",
            ),
            TraceStep(
                step="repair_context_evidence",
                status="success",
                duration_ms=0,
            ),
            TraceStep(
                step="repair_impact",
                status="success",
                duration_ms=0,
            ),
            TraceStep(
                step="repair_action",
                status="success",
                duration_ms=0,
            ),
            TraceStep(
                step="repair_blocked",
                status="success",
                duration_ms=0,
                fallback_used=True,
            ),
        ],
        runs=1,
        provider="mock-provider",
        model="mock-model",
        model_profile="mock-agent",
    )

    assert summary.repair_requested_count == 1
    assert summary.repair_executed_count == 3
    assert summary.repair_blocked_count == 1
    assert summary.repair_fallback_count == 1
