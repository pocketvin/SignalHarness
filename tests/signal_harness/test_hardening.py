from __future__ import annotations

import asyncio
import json
from pathlib import Path

from signal_harness.runtime.tools_base import ToolResult
from typer.testing import CliRunner

from signal_harness.cli import app
from signal_harness.runtime.workflow import SignalHarnessWorkflow


runner = CliRunner()


def test_since_accepts_trailing_z(project_root: Path, tmp_path: Path) -> None:
    result = runner.invoke(
        app,
        [
            "scan",
            "--fixture",
            str(project_root / "examples/signal_harness/sample_events.json"),
            "--since",
            "2026-06-24T00:00:00Z",
            "--cwd",
            str(project_root),
            "--output-dir",
            str(tmp_path / "outputs"),
            "--state-dir",
            str(tmp_path / "state"),
        ],
    )

    assert result.exit_code == 0, result.output


def test_since_rejects_invalid_value(project_root: Path, tmp_path: Path) -> None:
    result = runner.invoke(
        app,
        [
            "scan",
            "--fixture",
            str(project_root / "examples/signal_harness/sample_events.json"),
            "--since",
            "not-a-date",
            "--cwd",
            str(project_root),
            "--output-dir",
            str(tmp_path / "outputs"),
            "--state-dir",
            str(tmp_path / "state"),
        ],
    )

    assert result.exit_code != 0
    assert "Invalid value" in result.output
    assert "Usage:" in result.output
    assert "signal-harness scan" in result.output


def test_remote_failures_do_not_block_fixture_web_changes(
    project_root: Path,
    tmp_path: Path,
) -> None:
    output_dir = tmp_path / "outputs"
    workflow = SignalHarnessWorkflow(
        cwd=project_root,
        output_dir=output_dir,
        state_dir=tmp_path / "state",
    )
    original_call = workflow.executor.call

    async def partial_failure(name: str, arguments: dict[str, object]) -> ToolResult:
        if name in {"github_signal", "rss_signal"}:
            return ToolResult(output=f"{name} unavailable", is_error=True)
        return await original_call(name, arguments)

    workflow.executor.call = partial_failure  # type: ignore[method-assign]
    result = asyncio.run(workflow.scan())

    assert result.signals
    assert result.failed_sources
    assert any(task.status == "failed" for task in result.source_tasks)
    assert any(task.status == "success" for task in result.source_tasks)
    trace = json.loads((output_dir / "task_trace.json").read_text(encoding="utf-8"))
    collect = next(item for item in trace if item["step"] == "collect_signals")
    assert collect["failed_sources"]
    assert any(task["status"] == "failed" for task in collect["source_tasks"])
    assert any(task["status"] == "success" for task in collect["source_tasks"])
    summary = (output_dir / "run_summary.txt").read_text(encoding="utf-8")
    assert "Failed sources:" in summary
    assert "github_signal unavailable" in summary
    assert "status=failed" in summary


def test_json_outputs_are_written_once(project_root: Path, tmp_path: Path) -> None:
    workflow = SignalHarnessWorkflow(
        cwd=project_root,
        output_dir=tmp_path / "outputs",
        state_dir=tmp_path / "state",
    )
    original_call = workflow.executor.call
    json_write_calls = 0

    async def count_writes(name: str, arguments: dict[str, object]) -> ToolResult:
        nonlocal json_write_calls
        if name == "report_writer" and arguments.get("action") == "write_json_outputs":
            json_write_calls += 1
        return await original_call(name, arguments)

    workflow.executor.call = count_writes  # type: ignore[method-assign]
    asyncio.run(
        workflow.scan(
            fixture=project_root / "examples/signal_harness/sample_events.json"
        )
    )

    assert json_write_calls == 1


def test_trace_command_writes_markdown(project_root: Path, tmp_path: Path) -> None:
    output_dir = tmp_path / "outputs"
    scan_result = runner.invoke(
        app,
        [
            "scan",
            "--fixture",
            str(project_root / "examples/signal_harness/sample_events.json"),
            "--cwd",
            str(project_root),
            "--output-dir",
            str(output_dir),
            "--state-dir",
            str(tmp_path / "state"),
        ],
    )
    assert scan_result.exit_code == 0, scan_result.output

    trace_result = runner.invoke(
        app,
        [
            "trace",
            "--cwd",
            str(project_root),
            "--output-dir",
            str(output_dir),
        ],
    )

    assert trace_result.exit_code == 0, trace_result.output
    assert "load_config" in trace_result.output
    assert "evidence" in trace_result.output
    summary_path = output_dir / "trace_summary.md"
    assert summary_path.exists()
    assert "SignalHarness Trace Summary" in summary_path.read_text(encoding="utf-8")
