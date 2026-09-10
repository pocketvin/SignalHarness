from __future__ import annotations

import json
from pathlib import Path

import pytest

from signal_harness.runtime.tools_base import ToolExecutionContext
from signal_harness.tools.github_signal import (
    GitHubSignalTool,
    _github_authorization_header,
    _github_cli_authorization_header,
)
from signal_harness.tools.rss_signal import RssSignalTool, parse_feed
from signal_harness.tools.signal_memory import SignalMemoryTool
from signal_harness.tools.web_change import WebChangeTool


def test_parse_atom_feed() -> None:
    xml = """
    <feed xmlns="http://www.w3.org/2005/Atom">
      <entry>
        <title>Agent update</title>
        <summary>Structured evidence</summary>
        <link href="https://example.com/update"/>
        <updated>2026-06-25T00:00:00Z</updated>
        <id>item-1</id>
      </entry>
    </feed>
    """

    items = parse_feed(xml)

    assert items == [
        {
            "title": "Agent update",
            "summary": "Structured evidence",
            "link": "https://example.com/update",
            "published": "2026-06-25T00:00:00Z",
            "id": "item-1",
        }
    ]


@pytest.mark.asyncio
async def test_web_change_tool_loads_fixture(project_root: Path) -> None:
    tool = WebChangeTool()
    result = await tool.execute(
        tool.input_model(
            action="load_fixture",
            fixture="examples/signal_harness/sample_events.json",
        ),
        ToolExecutionContext(cwd=project_root),
    )

    assert result.is_error is False
    assert len(json.loads(result.output)) >= 4


@pytest.mark.asyncio
async def test_github_tool_normalizes_without_network(tmp_path: Path) -> None:
    tool = GitHubSignalTool()
    result = await tool.execute(
        tool.input_model(
            action="normalize_github_event",
            repo="example/repo",
            event_kind="github_release",
            raw={
                "tag_name": "v1",
                "name": "Checkpoint update",
                "html_url": "https://example.com/release",
            },
        ),
        ToolExecutionContext(cwd=tmp_path),
    )

    assert result.is_error is False
    assert json.loads(result.output)["source_type"] == "github_release"


def test_github_authorization_header_skips_non_ascii_env(monkeypatch: pytest.MonkeyPatch) -> None:
    from types import SimpleNamespace

    _github_cli_authorization_header.cache_clear()
    monkeypatch.setenv("GITHUB_TOKEN", "本地占位符")
    monkeypatch.delenv("GH_TOKEN", raising=False)
    monkeypatch.setattr(
        "signal_harness.tools.github_signal.subprocess.run",
        lambda *args, **kwargs: SimpleNamespace(returncode=1, stdout="", stderr=""),
    )

    assert _github_authorization_header() == ""


def test_github_authorization_header_falls_back_from_placeholder_to_gh_cli(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from types import SimpleNamespace

    _github_cli_authorization_header.cache_clear()
    monkeypatch.setenv("GITHUB_TOKEN", "本地占位符")
    monkeypatch.delenv("GH_TOKEN", raising=False)
    monkeypatch.setattr(
        "signal_harness.tools.github_signal.subprocess.run",
        lambda *args, **kwargs: SimpleNamespace(
            returncode=0, stdout="test-keyring-token\n", stderr=""
        ),
    )

    assert _github_authorization_header() == "Bearer test-keyring-token"


@pytest.mark.asyncio
async def test_rss_tool_normalizes_without_network(tmp_path: Path) -> None:
    tool = RssSignalTool()
    result = await tool.execute(
        tool.input_model(
            action="normalize_rss_item",
            feed_name="Expert Feed",
            raw={"title": "Evidence update", "link": "https://example.com"},
        ),
        ToolExecutionContext(cwd=tmp_path),
    )

    assert result.is_error is False
    assert json.loads(result.output)["source_name"] == "Expert Feed"


@pytest.mark.asyncio
async def test_signal_memory_loads_versioned_config(project_root: Path) -> None:
    tool = SignalMemoryTool()
    result = await tool.execute(
        tool.input_model(action="load_project_profile"),
        ToolExecutionContext(cwd=project_root),
    )

    assert result.is_error is False
    assert json.loads(result.output)["project_name"] == "SignalHarness"


@pytest.mark.asyncio
async def test_github_release_fetch_preserves_previous_tag_before_since_filter(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeResponse:
        def raise_for_status(self) -> None:
            return None

        def json(self):
            return [
                {
                    "id": 2,
                    "tag_name": "v2.0.0",
                    "published_at": "2026-06-24T10:00:00Z",
                },
                {
                    "id": 1,
                    "tag_name": "v1.9.0",
                    "published_at": "2026-06-01T10:00:00Z",
                },
            ]

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb) -> None:
            return None

        async def get(self, *args, **kwargs):
            return FakeResponse()

    monkeypatch.setattr(
        "signal_harness.tools.github_signal.httpx.AsyncClient",
        lambda *args, **kwargs: FakeClient(),
    )
    tool = GitHubSignalTool()
    result = await tool.execute(
        tool.input_model(
            action="fetch_repo_releases",
            repo="example/repo",
            since="2026-06-20T00:00:00Z",
        ),
        ToolExecutionContext(cwd=tmp_path),
    )

    payload = json.loads(result.output)
    assert len(payload) == 1
    assert payload[0]["tag_name"] == "v2.0.0"
    assert payload[0]["_previous_tag_name"] == "v1.9.0"


def test_web_change_module_imports_without_runtime_package_cycle() -> None:
    import importlib

    module = importlib.import_module("signal_harness.tools.web_change")
    runtime = importlib.import_module("signal_harness.runtime")
    assert module.WebChangeTool.name == "web_change"
    assert runtime.SignalToolExecutor is not None
    assert "web_change" in runtime.SIGNAL_TOOL_ALLOWLIST


@pytest.mark.asyncio
async def test_github_pagination_fetches_following_pages(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from signal_harness.tools import github_signal

    class FakeResponse:
        def __init__(self, payload: list[dict[str, object]], next_url: str | None) -> None:
            self._payload = payload
            self.links = {"next": {"url": next_url}} if next_url else {}

        def raise_for_status(self) -> None:
            return None

        def json(self) -> list[dict[str, object]]:
            return self._payload

    class FakeClient:
        def __init__(self, *args: object, **kwargs: object) -> None:
            del args, kwargs
            self.calls = 0

        async def __aenter__(self) -> "FakeClient":
            return self

        async def __aexit__(self, *args: object) -> None:
            del args

        async def get(self, url: str, **kwargs: object) -> FakeResponse:
            del kwargs
            self.calls += 1
            if self.calls == 1:
                return FakeResponse([{"id": index} for index in range(100)], "https://next")
            return FakeResponse([{"id": 100}], None)

    monkeypatch.setattr(github_signal.httpx, "AsyncClient", FakeClient)
    payload, metadata = await github_signal._fetch_paginated(
        endpoint="https://api.github.com/repos/example/repo/issues",
        params={"per_page": 100},
        headers={},
        since=None,
        releases=False,
    )

    assert len(payload) == 101
    assert metadata["pages_fetched"] == 2
    assert metadata["coverage_status"] == "complete"
    assert metadata["history_limited"] is False


@pytest.mark.asyncio
async def test_github_pagination_cap_reports_partial(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from signal_harness.tools import github_signal

    class FakeResponse:
        links = {"next": {"url": "https://next"}}

        def raise_for_status(self) -> None:
            return None

        def json(self) -> list[dict[str, object]]:
            return [{"id": 1}]

    class FakeClient:
        def __init__(self, *args: object, **kwargs: object) -> None:
            del args, kwargs

        async def __aenter__(self) -> "FakeClient":
            return self

        async def __aexit__(self, *args: object) -> None:
            del args

        async def get(self, url: str, **kwargs: object) -> FakeResponse:
            del url, kwargs
            return FakeResponse()

    monkeypatch.setattr(github_signal.httpx, "AsyncClient", FakeClient)
    monkeypatch.setattr(github_signal, "MAX_GITHUB_PAGES", 2)
    payload, metadata = await github_signal._fetch_paginated(
        endpoint="https://api.github.com/repos/example/repo/issues",
        params={"per_page": 100},
        headers={},
        since=None,
        releases=False,
    )

    assert len(payload) == 2
    assert metadata["pages_fetched"] == 2
    assert metadata["coverage_status"] == "partial"
    assert metadata["history_limited"] is True
    assert metadata["diagnostics"]


def test_github_authorization_header_reuses_gh_cli_when_env_is_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from types import SimpleNamespace

    _github_cli_authorization_header.cache_clear()
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.delenv("GH_TOKEN", raising=False)
    monkeypatch.setattr(
        "signal_harness.tools.github_signal.subprocess.run",
        lambda *args, **kwargs: SimpleNamespace(
            returncode=0,
            stdout="test-gh-cli-token\n",
            stderr="",
        ),
    )

    assert _github_authorization_header() == "Bearer test-gh-cli-token"
