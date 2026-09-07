from __future__ import annotations

import json
from pathlib import Path

import pytest

from signal_harness.runtime.tools_base import ToolExecutionContext
from signal_harness.signal.deduplicator import signal_fingerprint
from signal_harness.signal.normalizer import normalize_event
from signal_harness.tools import web_snapshot
from signal_harness.tools.web_change import WebChangeTool
from signal_harness.tools.web_snapshot import (
    assert_public_http_url,
    collect_web_change,
    commit_pending_web_snapshots,
    discard_pending_web_snapshots,
    normalize_web_text,
    snapshot_diff_summary,
)


def test_normalize_web_text_removes_script_and_style_noise() -> None:
    text = normalize_web_text(
        "<html><style>.x{}</style><script>ignore()</script><main><h1>Release notes</h1><p>Version 2 adds streaming.</p></main></html>"
    )
    assert "ignore" not in text
    assert ".x" not in text
    assert "Release notes" in text
    assert "Version 2 adds streaming." in text


def test_snapshot_diff_summary_reports_before_and_after() -> None:
    summary = snapshot_diff_summary("Version 1\nOld API", "Version 2\nNew API")
    assert "Before:" in summary
    assert "Old API" in summary
    assert "After:" in summary
    assert "New API" in summary


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "url",
    [
        "file:///etc/passwd",
        "http://127.0.0.1/",
        "http://169.254.169.254/latest/meta-data/",
        "http://localhost/",
        "https://user:pass@example.com/",
        "https://example.com:8443/",
    ],
)
async def test_web_snapshot_rejects_non_public_or_unsafe_targets(url: str) -> None:
    with pytest.raises(ValueError):
        await assert_public_http_url(url)


@pytest.mark.asyncio
async def test_collect_web_change_creates_baseline_then_emits_only_real_change(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    versions = iter(
        [
            ("https://example.com/changelog", "text/html", "<h1>Release</h1><p>Version 1</p>"),
            ("https://example.com/changelog", "text/html", "<h1>Release</h1><p>Version 1</p>"),
            (
                "https://example.com/changelog",
                "text/html",
                "<h1>Release</h1><p>Version 2 adds MCP streaming</p>",
            ),
            (
                "https://example.com/changelog",
                "text/html",
                "<h1>Release</h1><p>Version 3 changes tool transport</p>",
            ),
        ]
    )

    async def fake_fetch(url: str, *, max_bytes: int):
        del url, max_bytes
        return next(versions)

    monkeypatch.setattr(web_snapshot, "fetch_public_text", fake_fetch)
    first, meta1 = await collect_web_change(
        url="https://example.com/changelog",
        source_name="Example changelog",
        state_dir=tmp_path,
        official=True,
    )
    second, meta2 = await collect_web_change(
        url="https://example.com/changelog",
        source_name="Example changelog",
        state_dir=tmp_path,
        official=True,
    )
    third, meta3 = await collect_web_change(
        url="https://example.com/changelog",
        source_name="Example changelog",
        state_dir=tmp_path,
        official=True,
    )
    fourth, _ = await collect_web_change(
        url="https://example.com/changelog",
        source_name="Example changelog",
        state_dir=tmp_path,
        official=True,
    )

    assert first == [] and meta1["baseline_created"] is True
    assert second == [] and meta2["changed"] is False
    assert len(third) == 1 and meta3["changed"] is True
    assert "Before:" in third[0]["content"] and "After:" in third[0]["content"]
    assert third[0]["official"] is True
    event3 = normalize_event(third[0])
    event4 = normalize_event(fourth[0])
    assert signal_fingerprint(event3) != signal_fingerprint(event4)


@pytest.mark.asyncio
async def test_failed_scan_does_not_consume_web_snapshot_change(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    versions = iter(
        [
            ("https://example.com/docs", "text/html", "<p>Version 1</p>"),
            ("https://example.com/docs", "text/html", "<p>Version 2</p>"),
            ("https://example.com/docs", "text/html", "<p>Version 2</p>"),
        ]
    )

    async def fake_fetch(url: str, *, max_bytes: int):
        del url, max_bytes
        return next(versions)

    monkeypatch.setattr(web_snapshot, "fetch_public_text", fake_fetch)
    baseline, _ = await collect_web_change(
        url="https://example.com/docs",
        source_name="Docs",
        state_dir=tmp_path,
    )
    assert baseline == []

    failed_events, failed_meta = await collect_web_change(
        url="https://example.com/docs",
        source_name="Docs",
        state_dir=tmp_path,
        scan_id="failed-scan",
    )
    assert len(failed_events) == 1
    assert failed_meta["pending_snapshot_path"]
    discard_pending_web_snapshots(tmp_path, "failed-scan")

    retry_events, _ = await collect_web_change(
        url="https://example.com/docs",
        source_name="Docs",
        state_dir=tmp_path,
        scan_id="retry-scan",
    )
    assert len(retry_events) == 1
    assert commit_pending_web_snapshots(tmp_path, "retry-scan") == 1


@pytest.mark.asyncio
async def test_web_change_tool_uses_project_state_snapshot(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_collect(**kwargs):
        assert kwargs["state_dir"] == str(tmp_path / "state")
        return (
            [
                {
                    "source_type": "web_change",
                    "source_name": "Docs",
                    "title": "Docs changed",
                    "content": "After: new API",
                    "url": "https://example.com/docs",
                    "published_at": "2026-09-07T00:00:00Z",
                    "change_kind": "updated",
                    "official": True,
                    "current_hash": "abc",
                    "previous_hash": "def",
                }
            ],
            {"changed": True},
        )

    watchlist = tmp_path / "watchlist.yaml"
    watchlist.write_text(
        "web_changes:\n  sources:\n    - name: Docs\n      adapter: http\n      url: https://example.com/docs\n      official: true\n",
        encoding="utf-8",
    )
    monkeypatch.setattr("signal_harness.tools.web_change.collect_web_change", fake_collect)
    tool = WebChangeTool()
    result = await tool.execute(
        tool.input_model(
            action="fetch_snapshot",
            url="https://example.com/docs",
            source_name="Docs",
            official=True,
        ),
        ToolExecutionContext(
            cwd=tmp_path,
            metadata={
                "state_dir": str(tmp_path / "state"),
                "watchlist_path": str(watchlist),
            },
        ),
    )
    assert result.is_error is False
    payload = json.loads(result.output)
    assert payload[0]["title"] == "Docs changed"


@pytest.mark.asyncio
async def test_web_change_tool_blocks_url_not_approved_by_watchlist(tmp_path: Path) -> None:
    watchlist = tmp_path / "watchlist.yaml"
    watchlist.write_text(
        "web_changes:\n  sources:\n    - name: Approved\n      adapter: http\n      url: https://example.com/approved\n",
        encoding="utf-8",
    )
    tool = WebChangeTool()
    result = await tool.execute(
        tool.input_model(
            action="fetch_snapshot",
            url="https://example.com/invented",
            source_name="Invented",
        ),
        ToolExecutionContext(
            cwd=tmp_path,
            metadata={
                "state_dir": str(tmp_path / "state"),
                "watchlist_path": str(watchlist),
            },
        ),
    )
    assert result.is_error is True
    assert "not approved by the current project Watchlist" in result.output


@pytest.mark.asyncio
async def test_workflow_report_failure_does_not_consume_web_change(
    project_root: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import shutil
    import yaml

    from signal_harness.agent_integration.mode import RunMode
    from signal_harness.runtime.workflow import SignalHarnessWorkflow

    config = tmp_path / "configs"
    shutil.copytree(project_root / "configs", config)
    (config / "watchlist.yaml").write_text(
        yaml.safe_dump(
            {
                "web_changes": {
                    "sources": [
                        {
                            "name": "Official Docs",
                            "adapter": "http",
                            "url": "https://example.com/docs",
                            "official": True,
                        }
                    ]
                }
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    versions = iter(
        [
            ("https://example.com/docs", "text/html", "<p>Version 1</p>"),
            ("https://example.com/docs", "text/html", "<p>Version 2</p>"),
            ("https://example.com/docs", "text/html", "<p>Version 2</p>"),
        ]
    )

    async def fake_fetch(url: str, *, max_bytes: int):
        del url, max_bytes
        return next(versions)

    monkeypatch.setattr(web_snapshot, "fetch_public_text", fake_fetch)
    state_dir = tmp_path / "state"
    baseline = SignalHarnessWorkflow(
        cwd=project_root, config_dir=config, output_dir=tmp_path / "baseline",
        state_dir=state_dir, mode=RunMode.DEMO, project_id="signalharness"
    )
    baseline_result = await baseline.scan(scan_id="baseline-scan")
    assert baseline_result.signals == []

    failed = SignalHarnessWorkflow(
        cwd=project_root, config_dir=config, output_dir=tmp_path / "failed",
        state_dir=state_dir, mode=RunMode.DEMO, project_id="signalharness"
    )

    async def fail_outputs(*args: object, **kwargs: object) -> None:
        del args, kwargs
        raise RuntimeError("forced report failure")

    monkeypatch.setattr(failed, "_write_outputs", fail_outputs)
    with pytest.raises(RuntimeError, match="forced report failure"):
        await failed.scan(scan_id="failed-scan")

    retry = SignalHarnessWorkflow(
        cwd=project_root, config_dir=config, output_dir=tmp_path / "retry",
        state_dir=state_dir, mode=RunMode.DEMO, project_id="signalharness"
    )
    retry_result = await retry.scan(scan_id="retry-scan")
    assert len(retry_result.signals) == 1
    assert retry_result.signals[0].raw_payload["current_hash"]


def test_web_change_source_quality_respects_watchlist_authority() -> None:
    from datetime import datetime, timezone

    from signal_harness.signal.schemas import SignalEvent, SourceQuality
    from signal_harness.signal.source_authority import event_source_quality

    official = SignalEvent(
        event_id="web-official",
        source_type="web_change",
        source_name="Official docs",
        title="Docs changed",
        raw_payload={"official": True},
        collected_at=datetime.now(timezone.utc),
    )
    secondary = official.model_copy(
        update={"event_id": "web-secondary", "raw_payload": {"official": False}}
    )
    assert event_source_quality(official) is SourceQuality.OFFICIAL
    assert event_source_quality(secondary) is SourceQuality.SECONDARY


def test_web_change_permission_distinguishes_live_snapshot_from_fixture() -> None:
    from signal_harness.agent_integration.tool_loop import permission_action

    assert permission_action("web_change", {"action": "load_fixture"}) == "read_mock_web_change"
    assert permission_action("web_change", {"action": "fetch_snapshot"}) == "read_web_change"


@pytest.mark.asyncio
async def test_web_only_first_baseline_is_successful_zero_change_scan(
    project_root: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import shutil
    import yaml

    from signal_harness.agent_integration.mode import RunMode
    from signal_harness.runtime.workflow import SignalHarnessWorkflow

    config = tmp_path / "configs"
    shutil.copytree(project_root / "configs", config)
    (config / "watchlist.yaml").write_text(
        yaml.safe_dump(
            {
                "web_changes": {
                    "sources": [
                        {
                            "name": "Official Docs",
                            "adapter": "http",
                            "url": "https://example.com/docs",
                            "official": True,
                        }
                    ]
                }
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )

    async def baseline_collect(**kwargs):
        del kwargs
        return [], {"baseline_created": True, "changed": False}

    monkeypatch.setattr("signal_harness.tools.web_change.collect_web_change", baseline_collect)
    workflow = SignalHarnessWorkflow(
        cwd=project_root,
        config_dir=config,
        output_dir=tmp_path / "outputs",
        state_dir=tmp_path / "state",
        mode=RunMode.DEMO,
    )
    result = await workflow.scan()

    assert result.signals == []
    assert result.assessments == []
    assert len(result.source_tasks) == 1
    assert result.source_tasks[0].status == "success"
    assert result.source_tasks[0].output_count == 0
