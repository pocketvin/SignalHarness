from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from signal_harness.agent_integration.mode import RunMode
from signal_harness.persistence import ChangeLedger
from signal_harness.providers.task_policy import TaskPolicy
from signal_harness.runtime.workflow import SignalHarnessWorkflow
from signal_harness.service import create_app
from signal_harness.service_streaming import StreamRunManager
from test_environment_pipeline import ScriptedIntelligenceProvider


@pytest.mark.asyncio
async def test_user_cancel_marks_stream_and_scan_cancelled(
    project_root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    entered = asyncio.Event()
    blocker = asyncio.Event()

    async def blocked_scan(self: SignalHarnessWorkflow, **kwargs: Any) -> Any:
        scan_id = str(kwargs["scan_id"])
        self.ledger.begin_scan(
            scan_id=scan_id,
            project_id=self.project_id,
            collected_count=1,
            deduped_count=1,
        )
        entered.set()
        await blocker.wait()
        raise AssertionError("cancelled scan unexpectedly resumed")

    monkeypatch.setattr(SignalHarnessWorkflow, "scan", blocked_scan)
    manager = StreamRunManager(
        cwd=project_root,
        config_dir=project_root / "configs",
        output_dir=tmp_path / "outputs",
        state_dir=tmp_path / "state",
    )
    session = manager.start(
        source_mode="live",
        fixture=None,
        since=None,
        window_mode="since_last",
        mode=RunMode.AGENT,
        provider_id=None,
        intelligence_pipeline=True,
    )
    manager.ensure_started(session)
    await asyncio.wait_for(entered.wait(), timeout=2)

    cancelled = await manager.cancel(session.run_id, project_id=session.project.id)

    assert cancelled.status == "cancelled"
    assert cancelled.progress is not None
    assert cancelled.progress["status"] == "cancelled"
    assert cancelled.events[-1].event == "run.cancelled"
    assert cancelled.task is not None and cancelled.task.done()
    metadata = ChangeLedger(session.state_dir / "change_ledger.sqlite3").scan_metadata(session.run_id)
    assert metadata is not None
    assert metadata["status"] == "cancelled"
    assert metadata["error"] is None
    durable = json.loads((session.output_dir / "service_run.json").read_text())
    assert durable["status"] == "cancelled"
    assert durable["cancelled_by"] == "user"

    replay = session.subscribe()
    seen: list[str] = []
    async for event in replay:
        seen.append(event.event)
    assert seen[-1] == "run.cancelled"
    await manager.shutdown()


def test_product_cancel_endpoint_stops_active_run_and_preserves_saved_report(
    project_root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = []
    monkeypatch.setattr(TaskPolicy, "providers", lambda self, role: ["offline"])
    monkeypatch.setattr(
        TaskPolicy,
        "create_provider",
        lambda self, name, role: ScriptedIntelligenceProvider(calls),
    )

    async def blocked_scan(self: SignalHarnessWorkflow, **kwargs: Any) -> Any:
        scan_id = str(kwargs["scan_id"])
        self.ledger.begin_scan(
            scan_id=scan_id,
            project_id=self.project_id,
            collected_count=1,
            deduped_count=1,
        )
        await asyncio.Event().wait()
        raise AssertionError("cancelled scan unexpectedly resumed")

    monkeypatch.setattr(SignalHarnessWorkflow, "scan", blocked_scan)
    app = create_app(
        cwd=project_root,
        config_dir=project_root / "configs",
        output_dir=tmp_path / "outputs",
        state_dir=tmp_path / "state",
    )
    with TestClient(app) as client:
        project = client.get("/intelligence/meta").json()["default_project_id"]
        started = client.post(
            f"/intelligence/projects/{project}/scans",
            json={"window": "since_last"},
        )
        assert started.status_code == 202, started.text
        run = started.json()

        stopped = client.delete(
            f"/intelligence/projects/{project}/scans/{run['run_id']}"
        )
        assert stopped.status_code == 200, stopped.text
        assert stopped.json()["status"] == "cancelled"
        assert stopped.json()["progress"]["status"] == "cancelled"

        home = client.get(f"/intelligence/projects/{project}").json()
        assert home["active_run"] is None
        session = app.state.stream_manager.get(run["run_id"])
        assert session is not None
        assert session.events[-1].event == "run.cancelled"

        again = client.delete(
            f"/intelligence/projects/{project}/scans/{run['run_id']}"
        )
        assert again.status_code == 200, again.text
        assert again.json()["status"] == "cancelled"
