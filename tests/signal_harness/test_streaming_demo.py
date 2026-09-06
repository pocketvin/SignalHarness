from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncGenerator
from pathlib import Path
from typing import Any, cast

import pytest
from fastapi.testclient import TestClient

from signal_harness.agent_integration.mode import RunMode
from signal_harness.runtime.tracing import TraceRecorder
from signal_harness.service import create_app
from signal_harness.service_streaming import StreamEvent, StreamRunManager


def _sse_events(response: Any) -> list[dict[str, object]]:
    events: list[dict[str, object]] = []
    current: dict[str, object] = {}
    for line in response.iter_lines():
        if not line:
            if current:
                events.append(current)
                current = {}
            continue
        if line.startswith("id:"):
            current["id"] = int(line.split(":", 1)[1].strip())
        elif line.startswith("event:"):
            current["event"] = line.split(":", 1)[1].strip()
        elif line.startswith("data:"):
            current["data"] = json.loads(line.split(":", 1)[1].strip())
    if current:
        events.append(current)
    return events


def _event_id(event: dict[str, object]) -> int:
    value = event["id"]
    assert isinstance(value, int)
    return value


def test_trace_recorder_notifies_append_and_update() -> None:
    changes: list[tuple[str, int, str]] = []
    recorder = TraceRecorder(
        lambda kind, index, step: changes.append((kind, index, step.step))
    )
    with recorder.step("collect_signals") as state:
        state["output_count"] = 2

    recorder.steps[0] = recorder.steps[0].model_copy(update={"detail": "updated"})

    assert changes == [
        ("append", 0, "collect_signals"),
        ("update", 0, "collect_signals"),
    ]


def test_demo_page_and_metadata(project_root: Path, tmp_path: Path) -> None:
    app = create_app(
        cwd=project_root,
        output_dir=tmp_path / "outputs",
        state_dir=tmp_path / "state",
    )
    with TestClient(app) as client:
        page = client.get("/demo")
        assert page.status_code == 200
        assert "SignalHarness Flight Deck" in page.text
        assert "看 SignalHarness 如何一步一步做出决策" in page.text
        assert "离线五 Agent 演示（推荐）" in page.text
        assert "中文" in page.text
        assert "new EventSource" in page.text
        assert "live runtime evidence, not a simulated animation" in page.text

        meta = client.get("/demo/meta")
        assert meta.status_code == 200
        payload = meta.json()
        assert payload["regression"]["suite"] == "resume-v1"
        assert payload["regression"]["cases"] == 40
        assert payload["regression"]["passed"] is True
        assert payload["mcp"]["tool_count"] == 5
        assert payload["streaming"] == {
            "transport": "sse",
            "durability": "in-process",
        }
        assert payload["provider"]["ready"] is False
        assert payload["provider"]["verified"] is False
        assert payload["provider"]["reason"] == "missing_api_key"


def test_demo_metadata_reports_configured_provider_without_network_call(
    project_root: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LLM_API_KEY", "test-only-placeholder")
    monkeypatch.delenv("LLM_PROVIDER", raising=False)
    monkeypatch.delenv("LLM_MODEL_PROFILE", raising=False)
    app = create_app(
        cwd=project_root,
        output_dir=tmp_path / "outputs",
        state_dir=tmp_path / "state",
    )
    with TestClient(app) as client:
        meta_response = client.get("/demo/meta")
        assert "test-only-placeholder" not in meta_response.text
        assert "base_url" not in meta_response.text.lower()
        provider = meta_response.json()["provider"]
        assert provider["ready"] is True
        assert provider["verified"] is False
        assert provider["provider"] == "openai_compatible"
        assert provider["model"]
        assert provider["reason"] is None


def test_agent_stream_run_requires_provider_configuration(
    project_root: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    app = create_app(
        cwd=project_root,
        output_dir=tmp_path / "outputs",
        state_dir=tmp_path / "state",
    )
    with TestClient(app) as client:
        response = client.post("/stream-runs", json={"mode": "agent"})
        assert response.status_code == 409
        detail = response.json()["detail"]
        assert detail["code"] == "agent_provider_not_ready"
        assert detail["reason"] == "missing_api_key"
        assert "LLM_API_KEY" in detail["message"]


def test_stream_run_replays_trace_and_final_result(
    project_root: Path,
    tmp_path: Path,
) -> None:
    app = create_app(
        cwd=project_root,
        output_dir=tmp_path / "outputs",
        state_dir=tmp_path / "state",
    )
    with TestClient(app) as client:
        created = client.post("/stream-runs", json={"mode": "mock-agent"})
        assert created.status_code == 202, created.text
        run = created.json()
        run_id = run["run_id"]
        queued = client.get(f"/stream-runs/{run_id}")
        assert queued.status_code == 200
        assert queued.json()["status"] == "queued"

        with client.stream("GET", run["events_url"]) as response:
            assert response.status_code == 200
            assert response.headers["content-type"].startswith("text/event-stream")
            events = _sse_events(response)

        names = [event["event"] for event in events]
        assert names[0] == "run.created"
        assert "run.started" in names
        assert "trace.step" in names
        assert "trace.step.updated" in names
        assert names[-1] == "run.completed"
        ids = [_event_id(event) for event in events]
        assert ids == sorted(ids)
        assert len(ids) == len(set(ids))

        completed = events[-1]["data"]
        assert isinstance(completed, dict)
        assert completed["run"]["status"] == "success"
        assert len(completed["signals"]) == 4
        assert len(completed["assessments"]) == 4

        status = client.get(f"/stream-runs/{run_id}")
        assert status.status_code == 200
        assert status.json()["status"] == "success"

        assessments = client.get(f"/runs/{run_id}/assessments")
        assert assessments.status_code == 200
        assert assessments.json()["count"] == 4


def test_sse_last_event_id_replays_only_newer_events(
    project_root: Path,
    tmp_path: Path,
) -> None:
    app = create_app(
        cwd=project_root,
        output_dir=tmp_path / "outputs",
        state_dir=tmp_path / "state",
    )
    with TestClient(app) as client:
        created = client.post("/stream-runs", json={"mode": "mock-agent"}).json()
        with client.stream("GET", created["events_url"]) as first_response:
            first = _sse_events(first_response)
        assert first[-1]["event"] == "run.completed"
        pivot = _event_id(first[2])

        with client.stream(
            "GET",
            created["events_url"],
            headers={"Last-Event-ID": str(pivot)},
        ) as replay_response:
            replay = _sse_events(replay_response)

        assert replay
        assert all(_event_id(item) > pivot for item in replay)
        assert replay[-1]["event"] == "run.completed"

        invalid = client.get(
            created["events_url"],
            headers={"Last-Event-ID": "not-an-int"},
        )
        assert invalid.status_code == 400


@pytest.mark.asyncio
async def test_stream_disconnect_does_not_cancel_workflow(
    project_root: Path,
    tmp_path: Path,
) -> None:
    manager = StreamRunManager(
        cwd=project_root,
        config_dir=project_root / "configs",
        output_dir=tmp_path / "outputs",
        state_dir=tmp_path / "state",
    )
    session = manager.start(
        fixture=project_root / "examples/signal_harness/sample_events.json",
        mode=RunMode.MOCK_AGENT,
        max_events=None,
        max_events_per_source=None,
    )
    stream = session.subscribe(
        on_subscribe=lambda: manager.ensure_started(session),
    )
    first = await anext(stream)
    second = await anext(stream)
    assert first.event == "run.created"
    assert second.event == "run.started"

    await cast(AsyncGenerator[StreamEvent, None], stream).aclose()
    assert session.task is not None
    await asyncio.wait_for(session.task, timeout=5)
    assert session.status == "success"
    assert session.result is not None
    await manager.shutdown()
