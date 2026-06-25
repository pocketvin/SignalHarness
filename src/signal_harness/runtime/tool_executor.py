"""Validated execution wrapper for the SignalHarness tool registry."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from openharness.tools.base import ToolExecutionContext, ToolRegistry, ToolResult


class SignalToolExecutor:
    """Resolve, validate, and execute allowlisted OpenHarness tools."""

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
        tool = self.registry.get(name)
        if tool is None:
            return ToolResult(output=f"Tool is not enabled: {name}", is_error=True)
        try:
            parsed = tool.input_model.model_validate(arguments)
        except Exception as exc:
            return ToolResult(output=f"Invalid input for {name}: {exc}", is_error=True)
        return await tool.execute(parsed, self.context)
