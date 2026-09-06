from __future__ import annotations

import asyncio
import json
import shutil
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
    config_dir = tmp_path / "configs"
    shutil.copytree(project_root / "configs", config_dir)
    (config_dir / "watchlist.yaml").write_text(
        (project_root / "configs/watchlist_demo.yaml").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    workflow = SignalHarnessWorkflow(
        cwd=project_root,
        config_dir=config_dir,
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


def test_scan_max_events_limits_collected_events(
    project_root: Path,
    tmp_path: Path,
) -> None:
    output_dir = tmp_path / "outputs"
    result = runner.invoke(
        app,
        [
            "scan",
            "--fixture",
            str(project_root / "examples/signal_harness/sample_events.json"),
            "--max-events",
            "2",
            "--max-events-per-source",
            "2",
            "--cwd",
            str(project_root),
            "--output-dir",
            str(output_dir),
            "--state-dir",
            str(tmp_path / "state"),
        ],
    )

    assert result.exit_code == 0, result.output
    signals = json.loads((output_dir / "signals.json").read_text(encoding="utf-8"))
    trace = json.loads((output_dir / "task_trace.json").read_text(encoding="utf-8"))
    funnel = next(item for item in trace if item["step"] == "candidate_funnel")
    limits = funnel["metadata"]["candidate_funnel"]

    assert len(signals) == 2
    assert limits["strategy"] == "project_relevance_v2"
    assert limits["before_count"] >= 4
    assert limits["after_count"] == 2
    assert limits["dropped_count"] == limits["before_count"] - 2


def test_event_limit_preserves_source_type_diversity(project_root: Path, tmp_path: Path) -> None:
    workflow = SignalHarnessWorkflow(
        cwd=project_root,
        output_dir=tmp_path / "outputs",
        state_dir=tmp_path / "state",
    )
    events = [
        {
            "event_id": f"issue-{index}",
            "source_type": "github_issue",
            "source_name": f"repo-{index % 3}",
            "title": f"Issue {index}",
            "published_at": f"2026-07-01T0{index}:00:00Z",
        }
        for index in range(6)
    ]
    events.extend(
        [
            {
                "event_id": "release-1",
                "source_type": "github_release",
                "source_name": "repo-release",
                "title": "Release",
                "published_at": "2026-07-01T10:00:00Z",
            },
            {
                "event_id": "rss-1",
                "source_type": "rss",
                "source_name": "feed",
                "title": "RSS",
                "published_at": "2026-07-01T09:30:00Z",
            },
            {
                "event_id": "web-1",
                "source_type": "web_change",
                "source_name": "web",
                "title": "Web",
                "published_at": "2026-07-01T09:00:00Z",
            },
        ]
    )

    result = workflow._apply_event_limits(  # noqa: SLF001
        events,
        max_events=4,
        max_events_per_source=None,
    )

    assert {event["source_type"] for event in result.events} == {
        "github_issue",
        "github_release",
        "rss",
        "web_change",
    }
    assert result.metadata["source_counts_before"]
    assert result.metadata["source_counts_after"]


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
    asyncio.run(workflow.scan(fixture=project_root / "examples/signal_harness/sample_events.json"))

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
