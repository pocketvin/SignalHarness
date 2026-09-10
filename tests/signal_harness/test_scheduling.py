from __future__ import annotations

import asyncio
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from signal_harness.agent_integration.mode import RunMode
from signal_harness.monitoring.scheduler import (
    ScheduleManager,
    next_schedule_time,
    schedule_initial_lower,
)
from signal_harness.persistence import ChangeLedger
from signal_harness.runtime.workflow import CollectionBatch, SignalHarnessWorkflow
from signal_harness.service_streaming import StreamRunManager
from signal_harness.signal.schemas import SourceTask


def test_interval_and_local_time_schedule_resolution() -> None:
    now = datetime(2026, 9, 9, 1, 0, tzinfo=timezone.utc)

    assert next_schedule_time(cadence="12h", now=now) == now + timedelta(hours=12)
    assert next_schedule_time(cadence="24h", now=now) == now + timedelta(hours=24)
    # 10:00 in Tokyo: today's 09:00 has passed, so next run is tomorrow 09:00 JST.
    assert next_schedule_time(
        cadence="daily",
        now=now,
        timezone_name="Asia/Tokyo",
        local_time="09:00",
    ) == datetime(2026, 9, 10, 0, 0, tzinfo=timezone.utc)
    # 19:00 JST is still ahead on the same local day.
    assert next_schedule_time(
        cadence="daily",
        now=now,
        timezone_name="Asia/Tokyo",
        local_time="19:00",
    ) == datetime(2026, 9, 9, 10, 0, tzinfo=timezone.utc)


def test_schedule_checkpoint_is_separate_and_partial_does_not_advance(tmp_path: Path) -> None:
    ledger = ChangeLedger(tmp_path / "ledger.sqlite3")
    now = datetime(2026, 9, 9, 12, 0, tzinfo=timezone.utc)
    created = ledger.create_schedule(
        project_id="demo",
        cadence="12h",
        interval_minutes=720,
        local_time=None,
        timezone_name="UTC",
        mode="demo",
        provider_id=None,
        max_events=12,
        max_events_per_source=4,
        next_run_at=now,
    )
    schedule_id = created["schedule_id"]

    due = ledger.due_schedules(project_id="demo", now=now)
    assert [item["schedule_id"] for item in due] == [schedule_id]
    assert ledger.claim_schedule_run(
        project_id="demo",
        schedule_id=schedule_id,
        run_id="run-partial",
        now=now,
        next_run_at=now + timedelta(hours=12),
    )
    ledger.complete_schedule_run(
        project_id="demo",
        schedule_id=schedule_id,
        run_id="run-partial",
        status="success",
        coverage_status="partial",
        checkpoint_at=now,
    )

    after = ledger.schedule(project_id="demo", schedule_id=schedule_id)
    assert after is not None
    assert after["last_status"] == "partial"
    assert after["checkpoint_at"] is None
    assert ledger.get_interactive_checkpoint(project_id="demo") is None


def test_missed_run_catches_up_from_schedule_checkpoint() -> None:
    checkpoint = datetime(2026, 9, 7, 12, 0, tzinfo=timezone.utc)
    now = datetime(2026, 9, 9, 12, 0, tzinfo=timezone.utc)
    schedule = {
        "cadence": "12h",
        "interval_minutes": 720,
        "checkpoint_at": checkpoint.isoformat(),
    }

    assert schedule_initial_lower(schedule, now=now) == checkpoint
    # Missed intervals are coalesced into one catch-up scan. The next trigger is
    # scheduled after now rather than replaying a stack of stale timer ticks.
    assert next_schedule_time(cadence="12h", now=now) == now + timedelta(hours=12)


def _copy_config(project_root: Path, tmp_path: Path) -> Path:
    target = tmp_path / "configs"
    shutil.copytree(project_root / "configs", target)
    return target


def _event(event_id: str, published_at: datetime) -> dict[str, object]:
    return {
        "id": event_id,
        "source_type": "github_issue",
        "source_name": "example/project",
        "title": "Scheduled compatibility change",
        "content": "checkpoint persistence compatibility update",
        "url": f"https://example.com/{event_id}",
        "published_at": published_at.isoformat(),
        "created_at": published_at.isoformat(),
        "updated_at": published_at.isoformat(),
        "official": True,
    }


async def _wait_schedule_terminal(
    manager: ScheduleManager,
    *,
    project_id: str,
    schedule_id: str,
) -> dict[str, object]:
    for _ in range(100):
        current = manager.ledger(project_id).schedule(
            project_id=project_id,
            schedule_id=schedule_id,
        )
        if current is not None and current["last_status"] != "running":
            return current
        await asyncio.sleep(0.01)
    raise AssertionError("scheduled run did not reach a terminal schedule state")


@pytest.mark.asyncio
async def test_scheduled_scan_reuses_workflow_and_never_consumes_manual_checkpoint(
    project_root: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_dir = _copy_config(project_root, tmp_path)
    streams = StreamRunManager(
        cwd=project_root,
        config_dir=config_dir,
        output_dir=tmp_path / "outputs",
        state_dir=tmp_path / "state",
    )
    schedules = ScheduleManager(
        stream_manager=streams,
        config_dir=config_dir,
        state_dir=tmp_path / "state",
    )
    now = datetime(2026, 9, 9, 12, 0, tzinfo=timezone.utc)
    manual_checkpoint = now - timedelta(hours=3)
    ledger = schedules.ledger("signalharness")
    ledger.advance_interactive_checkpoint(
        project_id="signalharness",
        checkpoint_at=manual_checkpoint,
        scan_id="manual-prior",
    )

    async def fake_collect(
        self: SignalHarnessWorkflow,
        watchlist: dict[str, object],
        *,
        since: datetime | None,
        guard: object,
        profile: dict[str, object] | None = None,
    ) -> CollectionBatch:
        del self, watchlist, guard, profile
        assert since == now - timedelta(hours=12)
        task = SourceTask(
            task_id="scheduled-source",
            source_name="example/project",
            source_type="github_issue",
            status="success",
            coverage_status="complete",
            pages_fetched=1,
            output_count=1,
        )
        return CollectionBatch(
            events=[_event("scheduled-success", now - timedelta(hours=1))],
            failed_sources=[],
            source_tasks=[task],
        )

    monkeypatch.setattr(SignalHarnessWorkflow, "_collect_watchlist", fake_collect)
    schedule = schedules.create_schedule(
        project_id="signalharness",
        cadence="12h",
        timezone_name="UTC",
        local_time=None,
        mode=RunMode.DEMO,
        provider_id=None,
        max_events=12,
        max_events_per_source=4,
        now=now - timedelta(hours=12),
    )

    run_ids = await schedules.run_due(now=now)
    assert len(run_ids) == 1
    session = streams.get(run_ids[0])
    assert session is not None
    assert session.interactive is False
    assert session.schedule_id == schedule["schedule_id"]
    assert session.window_mode == "custom"
    assert session.until == now
    assert session.task is not None
    await session.task
    terminal = await _wait_schedule_terminal(
        schedules,
        project_id="signalharness",
        schedule_id=str(schedule["schedule_id"]),
    )

    assert terminal["last_status"] == "success"
    assert terminal["checkpoint_at"] == now.isoformat()
    assert ledger.get_interactive_checkpoint(project_id="signalharness") == manual_checkpoint
    assert session.result is not None
    assert session.result["interactive"] is False
    assert session.result["schedule_id"] == schedule["schedule_id"]
    await schedules.shutdown()
    await streams.shutdown()


@pytest.mark.asyncio
async def test_partial_scheduled_scan_keeps_old_schedule_checkpoint(
    project_root: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_dir = _copy_config(project_root, tmp_path)
    streams = StreamRunManager(
        cwd=project_root,
        config_dir=config_dir,
        output_dir=tmp_path / "outputs",
        state_dir=tmp_path / "state",
    )
    schedules = ScheduleManager(
        stream_manager=streams,
        config_dir=config_dir,
        state_dir=tmp_path / "state",
    )
    now = datetime(2026, 9, 9, 12, 0, tzinfo=timezone.utc)

    async def partial_collect(
        self: SignalHarnessWorkflow,
        watchlist: dict[str, object],
        *,
        since: datetime | None,
        guard: object,
        profile: dict[str, object] | None = None,
    ) -> CollectionBatch:
        del self, watchlist, since, guard, profile
        task = SourceTask(
            task_id="partial-source",
            source_name="example/project",
            source_type="github_issue",
            status="success",
            coverage_status="partial",
            pages_fetched=1,
            history_limited=True,
            output_count=1,
            diagnostics=["synthetic history cap"],
        )
        return CollectionBatch(
            events=[_event("scheduled-partial", now - timedelta(hours=1))],
            failed_sources=[],
            source_tasks=[task],
        )

    monkeypatch.setattr(SignalHarnessWorkflow, "_collect_watchlist", partial_collect)
    schedule = schedules.create_schedule(
        project_id="signalharness",
        cadence="24h",
        timezone_name="UTC",
        local_time=None,
        mode=RunMode.DEMO,
        provider_id=None,
        max_events=12,
        max_events_per_source=4,
        now=now - timedelta(hours=24),
    )
    run_ids = await schedules.run_due(now=now)
    session = streams.get(run_ids[0])
    assert session is not None and session.task is not None
    await session.task
    terminal = await _wait_schedule_terminal(
        schedules,
        project_id="signalharness",
        schedule_id=str(schedule["schedule_id"]),
    )

    assert terminal["last_status"] == "partial"
    assert terminal["checkpoint_at"] is None
    assert schedules.ledger("signalharness").get_interactive_checkpoint(
        project_id="signalharness"
    ) is None
    await schedules.shutdown()
    await streams.shutdown()


def test_schedule_rest_lifecycle(project_root: Path, tmp_path: Path) -> None:
    from fastapi.testclient import TestClient

    from signal_harness.service import create_app

    config_dir = _copy_config(project_root, tmp_path)
    app = create_app(
        cwd=project_root,
        config_dir=config_dir,
        output_dir=tmp_path / "outputs",
        state_dir=tmp_path / "state",
    )
    with TestClient(app) as client:
        created = client.post(
            "/projects/signalharness/schedules",
            json={
                "cadence": "daily",
                "timezone": "Asia/Tokyo",
                "local_time": "09:30",
                "mode": "demo",
                "max_events": 12,
                "max_events_per_source": 4,
            },
        )
        assert created.status_code == 201
        schedule = created.json()
        assert schedule["project_id"] == "signalharness"
        assert schedule["cadence"] == "daily"
        assert schedule["timezone"] == "Asia/Tokyo"
        assert schedule["local_time"] == "09:30"
        assert schedule["enabled"] is True
        assert schedule["checkpoint_at"] is None

        listed = client.get("/projects/signalharness/schedules")
        assert listed.status_code == 200
        assert listed.json()["count"] == 1
        assert listed.json()["schedules"][0]["schedule_id"] == schedule["schedule_id"]

        invalid = client.post(
            "/projects/signalharness/schedules",
            json={
                "cadence": "daily",
                "timezone": "Mars/Olympus",
                "local_time": "09:30",
                "mode": "demo",
            },
        )
        assert invalid.status_code == 400

        disabled = client.delete(
            f"/projects/signalharness/schedules/{schedule['schedule_id']}"
        )
        assert disabled.status_code == 200
        assert disabled.json()["enabled"] is False
        after = client.get("/projects/signalharness/schedules").json()["schedules"][0]
        assert after["enabled"] is False

@pytest.mark.asyncio
async def test_scheduled_success_invokes_inbox_projection(
    project_root: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_dir = _copy_config(project_root, tmp_path)
    streams = StreamRunManager(
        cwd=project_root,
        config_dir=config_dir,
        output_dir=tmp_path / "outputs",
        state_dir=tmp_path / "state",
    )
    schedules = ScheduleManager(
        stream_manager=streams,
        config_dir=config_dir,
        state_dir=tmp_path / "state",
    )
    now = datetime(2026, 9, 9, 12, 0, tzinfo=timezone.utc)

    async def fake_collect(
        self: SignalHarnessWorkflow,
        watchlist: dict[str, object],
        *,
        since: datetime | None,
        guard: object,
        profile: dict[str, object] | None = None,
    ) -> CollectionBatch:
        del self, watchlist, guard, profile
        task = SourceTask(
            task_id="scheduled-notify-source",
            source_name="example/project",
            source_type="github_issue",
            status="success",
            coverage_status="complete",
            pages_fetched=1,
            output_count=1,
        )
        return CollectionBatch(
            events=[_event("scheduled-notify", now - timedelta(hours=1))],
            failed_sources=[],
            source_tasks=[task],
        )

    seen_scans: list[str] = []

    class FakeNotificationService:
        def ingest_scan(self, *, scan_id: str) -> list[dict[str, object]]:
            seen_scans.append(scan_id)
            return []

        async def deliver_due(self) -> list[dict[str, object]]:
            return []

    monkeypatch.setattr(SignalHarnessWorkflow, "_collect_watchlist", fake_collect)
    monkeypatch.setattr(
        schedules,
        "notifications",
        lambda project_id: FakeNotificationService(),
    )
    schedule = schedules.create_schedule(
        project_id="signalharness",
        cadence="12h",
        timezone_name="UTC",
        local_time=None,
        mode=RunMode.DEMO,
        provider_id=None,
        max_events=12,
        max_events_per_source=4,
        now=now - timedelta(hours=12),
    )

    run_ids = await schedules.run_due(now=now)
    assert len(run_ids) == 1
    session = streams.get(run_ids[0])
    assert session is not None and session.task is not None
    await session.task
    await _wait_schedule_terminal(
        schedules,
        project_id="signalharness",
        schedule_id=str(schedule["schedule_id"]),
    )
    for _ in range(50):
        if seen_scans:
            break
        await asyncio.sleep(0.01)
    assert seen_scans == run_ids
    await schedules.shutdown()
    await streams.shutdown()

@pytest.mark.asyncio
async def test_schedule_reconcile_recovers_completed_run_metadata_after_restart(
    project_root: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import json

    config_dir = _copy_config(project_root, tmp_path)
    output_dir = tmp_path / "outputs"
    state_dir = tmp_path / "state"
    streams = StreamRunManager(
        cwd=project_root,
        config_dir=config_dir,
        output_dir=output_dir,
        state_dir=state_dir,
    )
    schedules = ScheduleManager(
        stream_manager=streams,
        config_dir=config_dir,
        state_dir=state_dir,
    )
    now = datetime(2026, 9, 9, 12, 0, tzinfo=timezone.utc)
    ledger = schedules.ledger("signalharness")
    manual_checkpoint = now - timedelta(hours=2)
    ledger.advance_interactive_checkpoint(
        project_id="signalharness",
        checkpoint_at=manual_checkpoint,
        scan_id="manual-before-restart",
    )
    schedule = schedules.create_schedule(
        project_id="signalharness",
        cadence="24h",
        timezone_name="UTC",
        local_time=None,
        mode=RunMode.DEMO,
        provider_id=None,
        max_events=12,
        max_events_per_source=4,
        now=now - timedelta(hours=24),
    )
    run_id = "run-restart-complete"
    assert ledger.claim_schedule_run(
        project_id="signalharness",
        schedule_id=str(schedule["schedule_id"]),
        run_id=run_id,
        now=now,
        next_run_at=now + timedelta(hours=24),
    )

    run_dir = output_dir / "service-runs" / run_id
    run_dir.mkdir(parents=True)
    (run_dir / "service_run.json").write_text(
        json.dumps(
            {
                "run_id": run_id,
                "status": "success",
                "project_id": "signalharness",
                "schedule_id": schedule["schedule_id"],
                "interactive": False,
                "coverage_status": "complete",
                "window": {
                    "mode": "custom",
                    "from": (now - timedelta(hours=24)).isoformat(),
                    "to": now.isoformat(),
                },
            }
        ),
        encoding="utf-8",
    )
    notified: list[str] = []

    class FakeNotificationService:
        def ingest_scan(self, *, scan_id: str) -> list[dict[str, object]]:
            notified.append(scan_id)
            return []

        async def deliver_due(self) -> list[dict[str, object]]:
            return []

    monkeypatch.setattr(
        schedules,
        "notifications",
        lambda project_id: FakeNotificationService(),
    )
    await schedules.reconcile_running()

    restored = ledger.schedule(
        project_id="signalharness",
        schedule_id=str(schedule["schedule_id"]),
    )
    assert restored is not None
    assert restored["last_status"] == "success"
    assert restored["checkpoint_at"] == now.isoformat()
    assert ledger.get_interactive_checkpoint(project_id="signalharness") == manual_checkpoint
    assert notified == [run_id]
