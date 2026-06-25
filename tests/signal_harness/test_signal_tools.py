from __future__ import annotations

import json
from pathlib import Path

import pytest

from openharness.tools.base import ToolExecutionContext
from signal_harness.tools.github_signal import GitHubSignalTool
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
