from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx
import pytest

from signal_harness.agent_integration.mode import RunMode
from signal_harness.runtime.tools_base import ToolExecutionContext, ToolResult
from signal_harness.runtime.workflow import SignalHarnessWorkflow
from signal_harness.tools import package_registry
from signal_harness.tools.package_registry import PackageRegistryTool


def _index_payload() -> dict[str, Any]:
    return {
        "meta": {"_last-serial": 12345, "api-version": "1.4"},
        "files": [
            {
                "filename": "mcp-2.2.0-py3-none-any.whl",
                "upload-time": "2026-09-08T10:00:00Z",
                "yanked": False,
            },
            {
                "filename": "mcp-2.2.0.tar.gz",
                "upload-time": "2026-09-08T10:00:02Z",
                "yanked": False,
            },
            {
                "filename": "mcp-2.1.1-py3-none-any.whl",
                "upload-time": "2026-08-01T09:00:00Z",
                "yanked": "superseded build",
            },
        ],
    }


def _mock_client(monkeypatch: pytest.MonkeyPatch, payload: dict[str, Any]) -> list[httpx.Request]:
    requests: list[httpx.Request] = []
    real_client = httpx.AsyncClient

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            json=payload,
            headers={"ETag": '"registry-v1"', "X-PyPI-Last-Serial": "12345"},
            request=request,
        )

    def factory(*args: object, **kwargs: object) -> httpx.AsyncClient:
        del args, kwargs
        return real_client(transport=httpx.MockTransport(handler))

    monkeypatch.setattr("signal_harness.tools.package_registry.httpx.AsyncClient", factory)
    return requests


@pytest.mark.asyncio
async def test_pypi_tool_groups_distribution_files_and_preserves_previous_version(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requests = _mock_client(monkeypatch, _index_payload())
    tool = PackageRegistryTool()
    result = await tool.execute(
        tool.input_model(
            package="MCP",
            since=datetime(2026, 9, 1, tzinfo=timezone.utc),
        ),
        ToolExecutionContext(cwd=tmp_path),
    )

    assert result.is_error is False
    rows = json.loads(result.output)
    assert len(rows) == 1
    assert rows[0]["package_name"] == "mcp"
    assert rows[0]["current_version"] == "2.2.0"
    assert rows[0]["previous_version"] == "2.1.1"
    assert rows[0]["file_count"] == 2
    assert rows[0]["yanked"] is False
    assert result.metadata["coverage_status"] == "complete"
    assert result.metadata["last_serial"] == 12345
    assert result.metadata["etag"] == '"registry-v1"'
    assert result.metadata["api_version"] == "1.4"
    assert requests[0].headers["Accept"] == package_registry.PYPI_SIMPLE_ACCEPT
    assert "SignalHarness" in requests[0].headers["User-Agent"]


@pytest.mark.asyncio
async def test_pypi_tool_rejects_unknown_future_major_version(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = _index_payload()
    payload["meta"]["api-version"] = "2.0"
    _mock_client(monkeypatch, payload)

    result = await PackageRegistryTool().execute(
        PackageRegistryTool.input_model(package="mcp"),
        ToolExecutionContext(cwd=tmp_path),
    )

    assert result.is_error is True
    assert "unsupported PyPI Simple API major version" in result.output


@pytest.mark.asyncio
async def test_pypi_tool_reports_permanent_http_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    real_client = httpx.AsyncClient

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, request=request)

    def factory(*args: object, **kwargs: object) -> httpx.AsyncClient:
        del args, kwargs
        return real_client(transport=httpx.MockTransport(handler))

    monkeypatch.setattr("signal_harness.tools.package_registry.httpx.AsyncClient", factory)
    result = await PackageRegistryTool().execute(
        PackageRegistryTool.input_model(package="missing-package"),
        ToolExecutionContext(cwd=tmp_path),
    )

    assert result.is_error is True
    assert "PyPI request failed" in result.output


@pytest.mark.asyncio
async def test_pypi_tool_retries_transient_status(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = 0
    real_client = httpx.AsyncClient

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(503, headers={"Retry-After": "0"}, request=request)
        return httpx.Response(200, json=_index_payload(), request=request)

    def factory(*args: object, **kwargs: object) -> httpx.AsyncClient:
        del args, kwargs
        return real_client(transport=httpx.MockTransport(handler))

    monkeypatch.setattr("signal_harness.tools.package_registry.httpx.AsyncClient", factory)
    payload, _ = await package_registry._fetch_pypi_simple("mcp")
    assert payload["meta"]["_last-serial"] == 12345
    assert calls == 2


def test_pypi_history_cap_is_truthful(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(package_registry, "MAX_PYPI_RELEASES", 2)
    payload = {
        "meta": {"_last-serial": 1},
        "files": [
            {
                "filename": f"demo-{version}-py3-none-any.whl",
                "upload-time": f"2026-09-0{day}T00:00:00Z",
                "yanked": False,
            }
            for version, day in (("3.0.0", 3), ("2.0.0", 2), ("1.0.0", 1))
        ],
    }
    rows, metadata = package_registry._release_rows("demo", payload, since=None)
    assert [row["current_version"] for row in rows] == ["3.0.0", "2.0.0"]
    assert metadata["coverage_status"] == "partial"
    assert metadata["history_limited"] is True
    assert metadata["diagnostics"]


def test_pypi_lineage_uses_pep440_version_order_not_upload_time() -> None:
    payload = {
        "meta": {"_last-serial": 9},
        "files": [
            {
                "filename": "pydantic-2.13.4-py3-none-any.whl",
                "upload-time": "2026-07-01T00:00:00Z",
                "yanked": False,
            },
            {
                "filename": "pydantic-2.14.0b1-py3-none-any.whl",
                "upload-time": "2026-08-01T00:00:00Z",
                "yanked": False,
            },
            {
                "filename": "pydantic-2.13.5-py3-none-any.whl",
                "upload-time": "2026-08-28T00:00:00Z",
                "yanked": False,
            },
        ],
    }
    rows, _ = package_registry._release_rows("pydantic", payload, since=None)
    by_version = {row["current_version"]: row for row in rows}
    assert by_version["2.13.5"]["previous_version"] == "2.13.4"
    assert by_version["2.14.0b1"]["previous_version"] == "2.13.5"


@pytest.mark.asyncio
async def test_workflow_collects_pypi_into_frozen_change_ledger(
    project_root: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _mock_client(monkeypatch, _index_payload())
    watchlist = tmp_path / "watchlist.yaml"
    watchlist.write_text(
        "package_registries:\n  pypi:\n    packages:\n      - name: mcp\n",
        encoding="utf-8",
    )
    workflow = SignalHarnessWorkflow(
        cwd=project_root,
        config_dir=project_root / "configs",
        project_profile_path=project_root / "configs/project_profile.yaml",
        watchlist_path=watchlist,
        output_dir=tmp_path / "outputs",
        state_dir=tmp_path / "state",
        mode=RunMode.DEMO,
        project_id="signalharness",
    )
    result = await workflow.scan(
        since=datetime(2026, 9, 1, tzinfo=timezone.utc),
        window_mode="custom",
        max_events=10,
    )

    assert result.coverage_status == "complete"
    assert result.all_change_count == 1
    assert result.signals[0].source_type == "package_registry"
    assert result.signals[0].source_name == "mcp"
    assert result.signals[0].current_version == "2.2.0"
    rows = workflow.ledger.list_scan_changes(result.scan_id, offset=0, limit=10)
    assert rows.count == 1
    assert rows.items[0]["source_type"] == "package_registry"
    coverage = workflow.ledger.source_coverage(result.scan_id)
    assert coverage[0]["source_type"] == "package_registry"
    assert coverage[0]["coverage_status"] == "complete"


@pytest.mark.asyncio
async def test_registry_failure_keeps_scan_partial_and_does_not_advance_checkpoint(
    project_root: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fail_registry(
        self: PackageRegistryTool,
        arguments: Any,
        context: ToolExecutionContext,
    ) -> ToolResult:
        del self, arguments, context
        return ToolResult(output="PyPI request failed: synthetic outage", is_error=True)

    monkeypatch.setattr(PackageRegistryTool, "execute", fail_registry)
    watchlist = tmp_path / "watchlist.yaml"
    watchlist.write_text(
        """package_registries:
  pypi:
    packages:
      - name: mcp
web_changes:
  sources:
    - name: regression-fixture
      adapter: fixture
      fixture: examples/signal_harness/sample_events.json
""",
        encoding="utf-8",
    )
    workflow = SignalHarnessWorkflow(
        cwd=project_root,
        config_dir=project_root / "configs",
        project_profile_path=project_root / "configs/project_profile.yaml",
        watchlist_path=watchlist,
        output_dir=tmp_path / "outputs",
        state_dir=tmp_path / "state",
        mode=RunMode.DEMO,
        project_id="signalharness",
    )

    result = await workflow.scan(window_mode="since_last", max_events=10)

    assert result.coverage_status == "partial"
    registry_task = next(
        task for task in result.source_tasks if task.source_type == "package_registry"
    )
    assert registry_task.status == "failed"
    assert registry_task.coverage_status == "partial"
    assert "synthetic outage" in (registry_task.error or "")
    assert workflow.ledger.get_interactive_checkpoint(project_id="signalharness") is None
