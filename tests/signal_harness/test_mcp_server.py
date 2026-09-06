from __future__ import annotations

import json
from pathlib import Path

import pytest
from mcp import Client

from signal_harness.mcp_server import build_mcp_server


def _write_artifacts(output_dir: Path, state_dir: Path) -> None:
    output_dir.mkdir(parents=True)
    state_dir.mkdir(parents=True)
    (output_dir / "impact_scores.json").write_text(
        json.dumps(
            [
                {
                    "event_id": "mcp-001",
                    "decision": "save",
                    "category": "tool_calling_signal",
                }
            ]
        ),
        encoding="utf-8",
    )
    (output_dir / "task_trace.json").write_text(
        json.dumps([{"step": "llm_agent_call", "agent_name": "ImpactAnalystAgent"}]),
        encoding="utf-8",
    )
    (state_dir / "signal_memory.json").write_text(
        json.dumps(
            {
                "seen_signals": ["mcp-001"],
                "duplicate_hashes": [],
                "previous_assessments": [
                    {
                        "event_id": "mcp-001",
                        "decision": "save",
                        "reason": "tool permission evidence",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    (state_dir / "feedback_memory.json").write_text("[]", encoding="utf-8")


@pytest.mark.asyncio
async def test_mcp_in_process_lists_and_calls_read_only_tools(
    project_root: Path,
    tmp_path: Path,
) -> None:
    output_dir = tmp_path / "outputs"
    state_dir = tmp_path / "state"
    _write_artifacts(output_dir, state_dir)
    server = build_mcp_server(
        cwd=project_root,
        output_dir=output_dir,
        state_dir=state_dir,
    )
    async with Client(server) as client:
        listed = await client.list_tools()
        names = {tool.name for tool in listed.tools}
        assert names == {
            "signalharness_get_project_context",
            "signalharness_search_signal_history",
            "signalharness_get_latest_assessments",
            "signalharness_get_run_trace",
            "signalharness_get_feedback_memory",
        }
        assert all(tool.annotations and tool.annotations.read_only_hint for tool in listed.tools)

        context = await client.call_tool("signalharness_get_project_context", {})
        assert context.is_error is False
        assert (context.structured_content or {})["project_profile"]["project_name"] == "SignalHarness"

        example_context = await client.call_tool(
            "signalharness_get_project_context",
            {"project_id": "example-agent-service"},
        )
        assert example_context.is_error is False
        example_payload = example_context.structured_content or {}
        assert example_payload["project_id"] == "example-agent-service"
        assert example_payload["project_profile"]["project_name"] == "Example Agent API Service"

        history = await client.call_tool(
            "signalharness_search_signal_history",
            {"query": "permission", "limit": 10},
        )
        assert history.is_error is False
        assert (history.structured_content or {})["count"] == 1

        trace = await client.call_tool(
            "signalharness_get_run_trace",
            {"agent": "ImpactAnalystAgent", "limit": 10},
        )
        assert trace.is_error is False
        assert (trace.structured_content or {})["count"] == 1


@pytest.mark.asyncio
async def test_mcp_rejects_path_traversal_run_id(
    project_root: Path,
    tmp_path: Path,
) -> None:
    output_dir = tmp_path / "outputs"
    state_dir = tmp_path / "state"
    _write_artifacts(output_dir, state_dir)
    server = build_mcp_server(
        cwd=project_root,
        output_dir=output_dir,
        state_dir=state_dir,
    )

    async with Client(server) as client:
        result = await client.call_tool(
            "signalharness_get_run_trace",
            {"run_id": "../secrets", "limit": 10},
        )

    assert result.is_error is True
    message = " ".join(item.text for item in result.content if hasattr(item, "text"))
    assert "Error executing tool signalharness_get_run_trace" in message
    assert "secrets" not in message
