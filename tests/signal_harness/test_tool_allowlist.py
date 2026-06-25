from __future__ import annotations

from pathlib import Path

import pytest

from openharness.api.client import ApiMessageCompleteEvent
from openharness.api.usage import UsageSnapshot
from openharness.engine.messages import ConversationMessage, TextBlock
from signal_harness.runtime.agent_loop import SignalAgentLoop
from signal_harness.runtime.tool_registry import (
    SIGNAL_TOOL_ALLOWLIST,
    create_signal_tool_registry,
)


class RecordingClient:
    def __init__(self) -> None:
        self.requests = []

    async def stream_message(self, request):
        self.requests.append(request)
        yield ApiMessageCompleteEvent(
            message=ConversationMessage(
                role="assistant",
                content=[TextBlock(text="Signal scan ready.")],
            ),
            usage=UsageSnapshot(input_tokens=1, output_tokens=1),
            stop_reason=None,
        )


def test_registry_exposes_only_signal_tools() -> None:
    registry = create_signal_tool_registry()
    names = {tool.name for tool in registry.list_tools()}

    assert names == SIGNAL_TOOL_ALLOWLIST
    assert registry.get("bash") is None
    assert registry.get("edit_file") is None
    assert registry.get("web_fetch") is None
    assert registry.get("mcp") is None


@pytest.mark.asyncio
async def test_openharness_agent_loop_receives_only_allowlisted_schemas(
    tmp_path: Path,
) -> None:
    client = RecordingClient()
    loop = SignalAgentLoop(
        api_client=client,
        cwd=tmp_path,
        model="test-model",
    )

    events = [event async for event in loop.submit("Assess project signals")]

    assert events
    assert {schema["name"] for schema in client.requests[0].tools} == SIGNAL_TOOL_ALLOWLIST
