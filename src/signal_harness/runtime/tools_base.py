"""Minimal local tool interfaces used by SignalHarness core.

These interfaces are intentionally small and local so offline demo,
mock-agent, and source tools do not depend on an external agent framework.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pydantic import BaseModel


@dataclass
class ToolExecutionContext:
    """Shared execution context for one tool invocation."""

    cwd: Path
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ToolResult:
    """Normalized tool execution result."""

    output: str
    is_error: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)


class BaseTool(ABC):
    """Base class for SignalHarness tools."""

    name: str
    description: str
    input_model: type[BaseModel]

    @abstractmethod
    async def execute(
        self,
        arguments: Any,
        context: ToolExecutionContext,
    ) -> ToolResult:
        """Execute the tool."""

    def is_read_only(self, arguments: Any) -> bool:
        """Return whether the invocation is read-only."""

        del arguments
        return False

    def to_api_schema(self) -> dict[str, Any]:
        """Return a simple tool schema for optional provider integrations."""

        return {
            "name": self.name,
            "description": self.description,
            "input_schema": self.input_model.model_json_schema(),
        }


class ToolRegistry:
    """Map tool names to implementations."""

    def __init__(self) -> None:
        self._tools: dict[str, BaseTool] = {}

    def register(self, tool: BaseTool) -> None:
        """Register a tool instance."""

        self._tools[tool.name] = tool

    def get(self, name: str) -> BaseTool | None:
        """Return a registered tool by name."""

        return self._tools.get(name)

    def list_tools(self) -> list[BaseTool]:
        """Return all registered tools."""

        return list(self._tools.values())

    def to_api_schema(self) -> list[dict[str, Any]]:
        """Return schemas for all registered tools."""

        return [tool.to_api_schema() for tool in self._tools.values()]
