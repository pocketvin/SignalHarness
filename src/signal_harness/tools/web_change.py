"""Fixture-backed web change collection with an adapter extension point."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

from openharness.tools.base import BaseTool, ToolExecutionContext, ToolResult


class WebChangeInput(BaseModel):
    action: Literal["load_mock_web_change_events", "load_fixture"] = "load_fixture"
    fixture: str = Field(description="JSON fixture path")


class WebChangeTool(BaseTool):
    """Load mock web-change events for deterministic local scans."""

    name = "web_change"
    description = "Load mock web-change or mixed sample events from a local JSON fixture."
    input_model = WebChangeInput

    def is_read_only(self, arguments: WebChangeInput) -> bool:
        return True

    async def execute(
        self,
        arguments: WebChangeInput,
        context: ToolExecutionContext,
    ) -> ToolResult:
        path = Path(arguments.fixture).expanduser()
        if not path.is_absolute():
            path = context.cwd / path
        path = path.resolve()
        try:
            path.relative_to(context.cwd.resolve())
        except ValueError:
            return ToolResult(output="Fixture must be inside the project workspace", is_error=True)
        if not path.exists():
            return ToolResult(output=f"Fixture not found: {path}", is_error=True)
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            return ToolResult(output=f"Fixture load failed: {exc}", is_error=True)
        if not isinstance(payload, list):
            return ToolResult(output="Fixture must contain a JSON list", is_error=True)
        return ToolResult(
            output=json.dumps(payload, ensure_ascii=False),
            metadata={"fixture": str(path), "event_count": len(payload)},
        )
