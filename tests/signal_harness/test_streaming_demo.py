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
    recorder = TraceRecorder(lambda kind, index, step: changes.append((kind, index, step.step)))
    with recorder.step("collect_signals") as state:
        state["output_count"] = 2

    recorder.steps[0] = recorder.steps[0].model_copy(update={"detail": "updated"})

    assert changes == [
        ("append", 0, "collect_signals"),
        ("update", 0, "collect_signals"),
    ]


def test_demo_page_and_metadata(
    project_root: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    monkeypatch.delenv("LLM_PROVIDER", raising=False)
    monkeypatch.delenv("LLM_MODEL_PROFILE", raising=False)
    for prefix in ("OPENAI", "QWEN", "KIMI", "DEEPSEEK"):
        monkeypatch.delenv(f"{prefix}_API_KEY", raising=False)
        monkeypatch.delenv(f"{prefix}_BASE_URL", raising=False)
        monkeypatch.delenv(f"{prefix}_MODEL", raising=False)
        monkeypatch.delenv(f"{prefix}_MODEL_PROFILE", raising=False)
    app = create_app(
        cwd=project_root,
        output_dir=tmp_path / "outputs",
        state_dir=tmp_path / "state",
    )
    with TestClient(app) as client:
        page = client.get("/demo")
        assert page.status_code == 200
        assert "SignalHarness · Project Environment Intelligence" in page.text
        assert '<div id="root"></div>' in page.text
        assert 'href="/demo-assets/demo.css"' in page.text
        assert 'src="/demo-assets/demo.js"' in page.text
        assert "<style>" not in page.text
        assert "面试演示建议" not in page.text
        assert "interview demos" not in page.text

        javascript = client.get("/demo-assets/demo.js")
        stylesheet = client.get("/demo-assets/demo.css")
        assert javascript.status_code == 200
        assert stylesheet.status_code == 200
        assert "EventSource" in javascript.text
        assert "/stream-runs" in javascript.text
        assert "/projects/connect/github" in javascript.text
        assert "/preferences/natural-language" in javascript.text
        assert "/product?top=12&all_limit=20" in javascript.text
        assert "/schedules" in javascript.text
        assert "/inbox" in javascript.text
        assert "/outcomes" in javascript.text
        assert "reasoning_summary" in javascript.text
        assert "reasoning_items" in javascript.text
        assert "Approval required before" not in javascript.text
        assert "--color-canvas" in stylesheet.text
        assert "prefers-reduced-motion" in stylesheet.text
        assert "user-scalable=no" not in page.text

        meta = client.get("/demo/meta")
        assert meta.status_code == 200
        payload = meta.json()
        assert payload["regression"]["suite"] == "resume-v1"
        assert payload["regression"]["cases"] == 40
        assert payload["regression"]["passed"] is True
        assert payload["mcp"]["tool_count"] == 10
        assert payload["mcp"]["read_only_tool_count"] == 9
        assert payload["mcp"]["write_tool_count"] == 1
        assert payload["streaming"] == {
            "transport": "sse",
            "durability": "persistent-run-retry",
            "event_replay": "in-process",
        }
        assert payload["providers"]
        assert payload["projects"]
        assert payload["default_project_id"] == "signalharness"
        projects = {item["id"]: item for item in payload["projects"]}
        assert projects["signalharness"]["name"] == "SignalHarness"
        assert projects["signalharness"]["watchlist"]["source_count"] == 15
        assert projects["example-agent-service"]["watchlist"]["source_count"] == 7
        assert payload["default_provider_id"] is None
        assert all(option["ready"] is False for option in payload["providers"])


def test_demo_metadata_reports_configured_provider_without_network_call(
    project_root: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("QWEN_API_KEY", "test-only-placeholder")
    monkeypatch.setenv("QWEN_BASE_URL", "https://example.test/v1")
    monkeypatch.setenv("QWEN_MODEL", "qwen-plus")
    monkeypatch.setenv("QWEN_MODEL_PROFILE", "qwen")
    monkeypatch.setenv("LLM_MODEL_PROFILE", "qwen")
    app = create_app(
        cwd=project_root,
        output_dir=tmp_path / "outputs",
        state_dir=tmp_path / "state",
    )
    with TestClient(app) as client:
        meta_response = client.get("/demo/meta")
        assert "test-only-placeholder" not in meta_response.text
        assert "base_url" not in meta_response.text.lower()
        payload = meta_response.json()
        providers = {item["id"]: item for item in payload["providers"]}
        assert providers["qwen"]["ready"] is True
        assert providers["qwen"]["model"] == "qwen-plus"
        assert providers["qwen"]["reason"] is None
        assert payload["default_provider_id"] == "qwen"


def test_agent_stream_run_requires_provider_configuration(
    project_root: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("QWEN_API_KEY", raising=False)
    monkeypatch.setenv("QWEN_BASE_URL", "https://example.test/v1")
    app = create_app(
        cwd=project_root,
        output_dir=tmp_path / "outputs",
        state_dir=tmp_path / "state",
    )
    with TestClient(app) as client:
        response = client.post(
            "/stream-runs", json={"mode": "agent", "provider_id": "qwen", "data_source": "fixture"}
        )
        assert response.status_code == 409
        detail = response.json()["detail"]
        assert detail["code"] == "agent_provider_not_ready"
        assert detail["reason"] == "missing_api_key"
        assert detail["provider_id"] == "qwen"


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
        created = client.post("/stream-runs", json={"mode": "mock-agent", "data_source": "fixture"})
        assert created.status_code == 202, created.text
        run = created.json()
        run_id = run["run_id"]
        queued = client.get(f"/stream-runs/{run_id}")
        assert queued.status_code == 200
        assert queued.json()["status"] in {"queued", "running", "success"}

        with client.stream("GET", run["events_url"]) as response:
            assert response.status_code == 200
            assert response.headers["content-type"].startswith("text/event-stream")
            events = _sse_events(response)

        names = [event["event"] for event in events]
        assert names[0] == "run.created"
        assert "run.started" in names
        assert "trace.step" in names
        assert "trace.step.updated" in names
        running_llm = [
            event
            for event in events
            if event["event"] == "trace.step"
            and isinstance(event.get("data"), dict)
            and isinstance(event["data"].get("trace"), dict)
            and event["data"]["trace"].get("step") == "llm_agent_call"
            and event["data"]["trace"].get("status") == "running"
        ]
        assert running_llm
        running_trace = running_llm[0]["data"]["trace"]
        assert running_trace["metadata"]["reasoning_state"] == "waiting_for_model"
        assert running_trace["metadata"]["reasoning_disclosure"] == (
            "structured_model_output_summary_not_hidden_chain_of_thought"
        )
        completed_llm = [
            event
            for event in events
            if event["event"] == "trace.step.updated"
            and isinstance(event.get("data"), dict)
            and isinstance(event["data"].get("trace"), dict)
            and event["data"]["trace"].get("step") == "llm_agent_call"
            and event["data"]["trace"].get("status") == "success"
            and event["data"]["trace"].get("metadata", {}).get("reasoning_state")
            in {"available", "fallback_output"}
        ]
        assert completed_llm
        assert all(
            event["data"]["trace"]["metadata"].get("reasoning_summary")
            for event in completed_llm
        )
        assert names[-1] == "run.completed"
        ids = [_event_id(event) for event in events]
        assert ids == sorted(ids)
        assert len(ids) == len(set(ids))

        completed = events[-1]["data"]
        assert isinstance(completed, dict)
        assert completed["run"]["status"] == "success"
        assert completed["run"]["project_id"] == "signalharness"
        assert completed["run"]["project_name"] == "SignalHarness"
        assert len(completed["signals"]) == 4
        assert len(completed["assessments"]) == 4

        status = client.get(f"/stream-runs/{run_id}")
        assert status.status_code == 200
        assert status.json()["status"] == "success"

        assessments = client.get(f"/runs/{run_id}/assessments")
        assert assessments.status_code == 200
        assert assessments.json()["count"] == 4

        product = client.get(f"/runs/{run_id}/product", params={"top": 2})
        assert product.status_code == 200
        product_payload = product.json()
        assert product_payload["scan_id"] == run_id
        assert product_payload["report"]["stats"]["all_change_count"] == 4
        assert sum(product_payload["report"]["stats"]["impact_group_counts"].values()) == 4
        assert product_payload["top_changes"]
        assert product_payload["top_changes"][0]["impact_group"]


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
        created = client.post(
            "/stream-runs", json={"mode": "mock-agent", "data_source": "fixture"}
        ).json()
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
        source_mode="fixture",
        fixture=project_root / "examples/signal_harness/sample_events.json",
        since=None,
        mode=RunMode.MOCK_AGENT,
        provider_id=None,
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


def test_stream_run_live_source_is_separate_from_fixture(
    project_root: Path,
    tmp_path: Path,
) -> None:
    app = create_app(
        cwd=project_root,
        output_dir=tmp_path / "outputs",
        state_dir=tmp_path / "state",
    )
    with TestClient(app) as client:
        response = client.post(
            "/stream-runs",
            json={"mode": "mock-agent", "data_source": "live", "since_days": 7},
        )
        assert response.status_code == 202
        payload = response.json()
        assert payload["status"] == "queued"
        assert payload["source_mode"] == "live"
        assert payload["project_id"] == "signalharness"
        assert payload["project_name"] == "SignalHarness"
        assert payload["since"] is not None


def test_stream_run_accepts_selected_configured_provider_without_calling_it(
    project_root: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-only-placeholder")
    monkeypatch.setenv("DEEPSEEK_BASE_URL", "https://example.test/v1")
    monkeypatch.setenv("DEEPSEEK_MODEL", "deepseek-chat")
    monkeypatch.setenv("DEEPSEEK_MODEL_PROFILE", "deepseek")
    app = create_app(
        cwd=project_root,
        output_dir=tmp_path / "outputs",
        state_dir=tmp_path / "state",
    )
    with TestClient(app) as client:
        response = client.post(
            "/stream-runs",
            json={
                "mode": "agent",
                "provider_id": "deepseek",
                "data_source": "fixture",
            },
        )
        assert response.status_code == 202
        payload = response.json()
        assert payload["provider_id"] == "deepseek"
        assert payload["source_mode"] == "fixture"


def test_demo_metadata_resolves_deprecated_kimi_without_exposing_secret(
    project_root: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("KIMI_API_KEY", "test-only-kimi-secret")
    monkeypatch.setenv("KIMI_BASE_URL", "https://kimi.example/v1")
    monkeypatch.setenv("KIMI_MODEL", "kimi-latest")
    monkeypatch.setenv("KIMI_MODEL_PROFILE", "kimi")
    app = create_app(
        cwd=project_root,
        output_dir=tmp_path / "outputs",
        state_dir=tmp_path / "state",
    )

    with TestClient(app) as client:
        response = client.get("/demo/meta")
        assert response.status_code == 200
        assert "test-only-kimi-secret" not in response.text
        providers = {item["id"]: item for item in response.json()["providers"]}
        kimi = providers["kimi"]
        assert kimi["ready"] is True
        assert kimi["model"] == "kimi-k3"
        assert kimi["warning"] == "deprecated_model_auto_upgraded"
        assert kimi["checked_at"] == "2026-09-11"


def test_stream_run_starts_without_sse_subscription(
    project_root: Path,
    tmp_path: Path,
) -> None:
    import time

    app = create_app(
        cwd=project_root,
        output_dir=tmp_path / "outputs",
        state_dir=tmp_path / "state",
    )
    with TestClient(app) as client:
        created = client.post(
            "/stream-runs",
            json={"mode": "mock-agent", "data_source": "fixture"},
        )
        assert created.status_code == 202
        run_id = created.json()["run_id"]
        status = "queued"
        for _ in range(100):
            payload = client.get(f"/stream-runs/{run_id}").json()
            status = payload["status"]
            if status in {"success", "error"}:
                break
            time.sleep(0.01)

        assert status == "success"
        persisted = client.get(f"/runs/{run_id}")
        assert persisted.status_code == 200
        assert persisted.json()["status"] == "success"


@pytest.mark.asyncio
async def test_stream_manager_recovers_persisted_queued_run(
    project_root: Path,
    tmp_path: Path,
) -> None:
    output_dir = tmp_path / "outputs"
    state_dir = tmp_path / "state"
    fixture = project_root / "examples" / "signal_harness" / "sample_events.json"

    first = StreamRunManager(
        cwd=project_root,
        config_dir=project_root / "configs",
        output_dir=output_dir,
        state_dir=state_dir,
    )
    queued = first.start(
        source_mode="fixture",
        fixture=fixture,
        since=None,
        mode=RunMode.DEMO,
        provider_id=None,
    )
    metadata = json.loads((queued.output_dir / "service_run.json").read_text(encoding="utf-8"))
    assert metadata["status"] == "queued"
    assert metadata["attempt"] == 0

    second = StreamRunManager(
        cwd=project_root,
        config_dir=project_root / "configs",
        output_dir=output_dir,
        state_dir=state_dir,
    )
    recovered = await second.recover_pending()
    assert recovered == 1
    restored = second.get(queued.run_id)
    assert restored is not None
    assert restored.task is not None
    await restored.task

    assert restored.status == "success"
    assert restored.attempt == 1
    final = json.loads((restored.output_dir / "service_run.json").read_text(encoding="utf-8"))
    assert final["status"] == "success"
    assert final["run_id"] == queued.run_id


@pytest.mark.asyncio
async def test_sse_waits_for_terminal_event_even_if_status_flips_first(
    project_root: Path, tmp_path: Path
) -> None:
    manager = StreamRunManager(
        cwd=project_root,
        config_dir=project_root / "configs",
        output_dir=tmp_path / "outputs",
        state_dir=tmp_path / "state",
    )
    session = manager.start(
        source_mode="fixture",
        fixture=project_root / "examples/signal_harness/sample_events.json",
        since=None,
        mode=RunMode.MOCK_AGENT,
        provider_id=None,
    )
    stream = session.subscribe()
    first = await anext(stream)
    assert first.event == "run.created"

    session.status = "success"

    async def publish_terminal() -> None:
        await asyncio.sleep(0)
        session.publish("run.completed", {"run": {"status": "success"}})

    producer = asyncio.create_task(publish_terminal())
    terminal = await asyncio.wait_for(anext(stream), timeout=1)
    await producer
    assert terminal.event == "run.completed"
    await stream.aclose()


def test_stream_run_accepts_custom_recent_day_window(
    project_root: Path,
    tmp_path: Path,
) -> None:
    app = create_app(
        cwd=project_root,
        output_dir=tmp_path / "outputs",
        state_dir=tmp_path / "state",
    )
    with TestClient(app) as client:
        response = client.post(
            "/stream-runs",
            json={"mode": "demo", "data_source": "fixture", "since_days": 90},
        )
        assert response.status_code == 202, response.text
        assert response.json()["project_id"] == "signalharness"
