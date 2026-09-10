"""SignalHarness-specific tool registry assembly."""

from __future__ import annotations

from signal_harness.runtime.tools_base import ToolRegistry
from signal_harness.tools import (
    GitHubSignalTool,
    LocalGitTool,
    PackageRegistryTool,
    ReportWriterTool,
    RssSignalTool,
    SecurityOsvTool,
    SignalMemoryTool,
    SignalScoreTool,
    WebChangeTool,
)

SIGNAL_TOOL_ALLOWLIST = frozenset(
    {
        "github_signal",
        "local_git",
        "package_registry",
        "rss_signal",
        "security_osv",
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
        LocalGitTool(),
        PackageRegistryTool(),
        RssSignalTool(),
        SecurityOsvTool(),
        WebChangeTool(),
        SignalMemoryTool(),
        SignalScoreTool(),
        ReportWriterTool(),
    ):
        registry.register(tool)
    return registry
