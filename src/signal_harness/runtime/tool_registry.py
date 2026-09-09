"""SignalHarness-specific tool registry assembly."""

from __future__ import annotations

from signal_harness.runtime.tools_base import ToolRegistry
from signal_harness.tools import (
    GitHubSignalTool,
    PackageRegistryTool,
    ReportWriterTool,
    RssSignalTool,
    SignalMemoryTool,
    SignalScoreTool,
    WebChangeTool,
)

SIGNAL_TOOL_ALLOWLIST = frozenset(
    {
        "github_signal",
        "package_registry",
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
        PackageRegistryTool(),
        RssSignalTool(),
        WebChangeTool(),
        SignalMemoryTool(),
        SignalScoreTool(),
        ReportWriterTool(),
    ):
        registry.register(tool)
    return registry
