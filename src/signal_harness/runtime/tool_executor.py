"""Validated execution wrapper for the SignalHarness tool registry."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from signal_harness.runtime.tools_base import ToolExecutionContext, ToolRegistry, ToolResult


_OFFLINE_FIXTURE_SOURCE_TOOLS = frozenset(
    {
        "github_signal",
        "rss_signal",
        "web_change",
        "package_registry",
        "security_osv",
    }
)


class SignalToolExecutor:
    """Resolve, validate, and execute allowlisted SignalHarness tools."""

    def __init__(
        self,
        registry: ToolRegistry,
        *,
        cwd: str | Path,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        self.registry = registry
        self.context = ToolExecutionContext(
            cwd=Path(cwd).expanduser().resolve(),
            metadata=dict(metadata or {}),
        )

    async def call(self, name: str, arguments: dict[str, Any]) -> ToolResult:
        explicit_mock = (
            arguments.get("mock_tool_eval") is True
            and self.context.metadata.get("allow_mock_tool_eval") is True
        )
        local_fixture_read = (
            name == "web_change" and arguments.get("action") == "load_fixture"
        )
        frozen_source_mock = (
            self.context.metadata.get("offline_fixture_source_tools") is True
            and name in _OFFLINE_FIXTURE_SOURCE_TOOLS
            and not local_fixture_read
        )
        if explicit_mock or frozen_source_mock:
            payload = {
                "tool": name,
                "mocked": True,
                "offline_fixture": frozen_source_mock,
                "source_type": arguments.get("source_type"),
                "event_ids": arguments.get("event_ids", []),
                "summary": (
                    "Fixture-safe frozen source observation generated without external I/O."
                    if frozen_source_mock
                    else "Fixture-safe mock tool observation generated for scripted mock-agent evaluation."
                ),
            }
            return ToolResult(
                output=json.dumps(payload, ensure_ascii=False),
                metadata={
                    "mock_tool_eval": True,
                    "offline_fixture": frozen_source_mock,
                    "tool": name,
                },
            )
        if (
            arguments.get("mock_tool_error") is True
            and self.context.metadata.get("allow_mock_tool_eval") is True
        ):
            return ToolResult(
                output=f"Mock tool failure for {name}",
                is_error=True,
                metadata={"mock_tool_eval": True, "tool": name},
            )
        tool = self.registry.get(name)
        if tool is None:
            return ToolResult(output=f"Tool is not enabled: {name}", is_error=True)
        try:
            parsed = tool.input_model.model_validate(arguments)
        except Exception as exc:
            return ToolResult(output=f"Invalid input for {name}: {exc}", is_error=True)
        return await tool.execute(parsed, self.context)
