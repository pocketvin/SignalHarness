"""SignalHarness-specific OpenHarness ToolRegistry assembly."""

from __future__ import annotations

from openharness.tools.base import ToolRegistry
from signal_harness.tools import (
    GitHubSignalTool,
    ReportWriterTool,
    RssSignalTool,
    SignalMemoryTool,
    SignalScoreTool,
    WebChangeTool,
)

SIGNAL_TOOL_ALLOWLIST = frozenset(
    {
        "github_signal",
        "rss_signal",
        "web_change",
        "signal_memory",
        "signal_score",
        "report_writer",
    }
)


def create_signal_tool_registry() -> ToolRegistry:
    """Return a registry that exposes only SignalHarness business tools."""

    registry = ToolRegistry()
    for tool in (
        GitHubSignalTool(),
        RssSignalTool(),
        WebChangeTool(),
        SignalMemoryTool(),
        SignalScoreTool(),
        ReportWriterTool(),
    ):
        registry.register(tool)
    return registry
