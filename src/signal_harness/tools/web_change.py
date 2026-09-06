"""Fixture-backed web change collection with an adapter extension point."""

from __future__ import annotations

import json
from typing import Literal

from pydantic import BaseModel, Field

from signal_harness.runtime.tools_base import BaseTool, ToolExecutionContext, ToolResult
from signal_harness.resources import is_allowed_fixture_path, resolve_example_path


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
        path = resolve_example_path(context.cwd, arguments.fixture)
        if not is_allowed_fixture_path(path, context.cwd):
            return ToolResult(
                output="Fixture must be inside the project workspace or packaged examples",
                is_error=True,
            )
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
