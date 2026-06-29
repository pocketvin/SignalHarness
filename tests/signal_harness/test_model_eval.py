from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from signal_harness.cli import app

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
