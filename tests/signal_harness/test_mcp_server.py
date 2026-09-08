from __future__ import annotations

import asyncio
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
    project_state = state_dir / "projects" / "signalharness"
    _write_artifacts(output_dir, project_state)
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
            "signalharness_start_scan",
            "signalharness_get_scan_status",
            "signalharness_get_product",
            "signalharness_list_changes",
            "signalharness_get_change_detail",
        }
        tools = {tool.name: tool for tool in listed.tools}
        start_annotations = tools["signalharness_start_scan"].annotations
        assert start_annotations is not None
        assert start_annotations.read_only_hint is False
        assert start_annotations.destructive_hint is False
        assert start_annotations.idempotent_hint is False
        assert start_annotations.open_world_hint is True
        assert all(
            tool.annotations and tool.annotations.read_only_hint
            for name, tool in tools.items()
            if name != "signalharness_start_scan"
        )

        context = await client.call_tool("signalharness_get_project_context", {})
        assert context.is_error is False
        assert (context.structured_content or {})["project_profile"][
            "project_name"
        ] == "SignalHarness"

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
            {"project_id": "signalharness", "query": "permission", "limit": 10},
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
    _write_artifacts(output_dir, state_dir / "projects" / "signalharness")
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


@pytest.mark.asyncio
async def test_mcp_starts_persistent_fixture_scan_and_reads_shared_product(
    project_root: Path,
    tmp_path: Path,
) -> None:
    output_dir = tmp_path / "outputs"
    state_dir = tmp_path / "state"
    server = build_mcp_server(
        cwd=project_root,
        output_dir=output_dir,
        state_dir=state_dir,
    )

    async with Client(server) as client:
        started = await client.call_tool(
            "signalharness_start_scan",
            {
                "project_id": "signalharness",
                "source_mode": "fixture",
                "mode": "demo",
                "max_events": 2,
                "max_events_per_source": 2,
            },
        )
        assert started.is_error is False
        start_payload = started.structured_content or {}
        run_id = str(start_payload["run_id"])
        assert start_payload["status"] in {"queued", "running"}
        assert start_payload["product_url"] == f"/runs/{run_id}/product"

        status_payload: dict[str, object] = {}
        for _ in range(100):
            status = await client.call_tool(
                "signalharness_get_scan_status", {"run_id": run_id}
            )
            assert status.is_error is False
            status_payload = dict(status.structured_content or {})
            if status_payload.get("status") in {"success", "error"}:
                break
            await asyncio.sleep(0.01)
        assert status_payload["status"] == "success"
        assert status_payload["product_ready"] is True

        product = await client.call_tool(
            "signalharness_get_product",
            {"run_id": run_id, "top": 1, "all_limit": 2},
        )
        assert product.is_error is False
        product_payload = product.structured_content or {}
        assert product_payload["scan_id"] == run_id
        assert product_payload["report"]["stats"]["all_change_count"] == 4
        assert product_payload["all_changes"]["all_count"] == 4
        assert len(product_payload["top_changes"]) == 1

        changes = await client.call_tool(
            "signalharness_list_changes",
            {"run_id": run_id, "analysis": "unanalyzed", "limit": 10},
        )
        assert changes.is_error is False
        changes_payload = changes.structured_content or {}
        assert changes_payload["count"] == 2

        first_id = product_payload["all_changes"]["items"][0]["change_id"]
        detail = await client.call_tool(
            "signalharness_get_change_detail",
            {"run_id": run_id, "change_id": first_id},
        )
        assert detail.is_error is False
        assert (detail.structured_content or {})["change_id"] == first_id

    assert (output_dir / "service-runs" / run_id / "service_run.json").is_file()


@pytest.mark.asyncio
async def test_mcp_completed_scan_status_and_product_survive_server_rebuild(
    project_root: Path,
    tmp_path: Path,
) -> None:
    output_dir = tmp_path / "outputs"
    state_dir = tmp_path / "state"
    first_server = build_mcp_server(
        cwd=project_root,
        output_dir=output_dir,
        state_dir=state_dir,
    )
    async with Client(first_server) as client:
        started = await client.call_tool(
            "signalharness_start_scan",
            {"source_mode": "fixture", "mode": "demo", "max_events": 2},
        )
        run_id = str((started.structured_content or {})["run_id"])
        for _ in range(100):
            status = await client.call_tool(
                "signalharness_get_scan_status", {"run_id": run_id}
            )
            if (status.structured_content or {}).get("status") == "success":
                break
            await asyncio.sleep(0.01)
        else:
            raise AssertionError("fixture scan did not complete")

    rebuilt = build_mcp_server(
        cwd=project_root,
        output_dir=output_dir,
        state_dir=state_dir,
    )
    async with Client(rebuilt) as client:
        status = await client.call_tool(
            "signalharness_get_scan_status", {"run_id": run_id}
        )
        assert status.is_error is False
        assert (status.structured_content or {})["status"] == "success"
        product = await client.call_tool(
            "signalharness_get_product", {"run_id": run_id, "top": 2}
        )
        assert product.is_error is False
        assert (product.structured_content or {})["scan_id"] == run_id


@pytest.mark.asyncio
async def test_mcp_start_scan_rejects_unsafe_fixture(
    project_root: Path,
    tmp_path: Path,
) -> None:
    outside = tmp_path / "outside.json"
    outside.write_text("[]", encoding="utf-8")
    server = build_mcp_server(
        cwd=project_root,
        output_dir=tmp_path / "outputs",
        state_dir=tmp_path / "state",
    )
    async with Client(server) as client:
        result = await client.call_tool(
            "signalharness_start_scan",
            {"source_mode": "fixture", "fixture": str(outside), "mode": "demo"},
        )
    assert result.is_error is True
