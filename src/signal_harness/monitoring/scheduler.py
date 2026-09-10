"""Persistent local scheduler that delegates every run to StreamRunManager."""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any, Literal, cast
from uuid import uuid4
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from signal_harness.agent_integration.mode import RunMode
from signal_harness.monitoring.notifications import NotificationService, SignedWebhookConfig
from signal_harness.persistence import ChangeLedger
from signal_harness.projects.catalog import ProjectOption, project_catalog, project_option
from signal_harness.projects.state import prepare_project_state
from signal_harness.service_streaming import StreamRunManager, StreamRunSession
from signal_harness.signal.policy import load_signal_policy

ScheduleCadence = Literal["12h", "24h", "daily"]


def next_schedule_time(
    *,
    cadence: ScheduleCadence,
    now: datetime,
    timezone_name: str = "UTC",
    local_time: str | None = None,
) -> datetime:
    """Return the next UTC trigger strictly after ``now``."""

    current = _utc(now)
    if cadence == "12h":
        return current + timedelta(hours=12)
    if cadence == "24h":
        return current + timedelta(hours=24)
    if cadence != "daily":
        raise ValueError(f"unsupported schedule cadence: {cadence}")
    if not local_time:
        raise ValueError("daily schedule requires local_time")
    hour, minute = _parse_local_time(local_time)
    try:
        zone = ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError as exc:
        raise ValueError(f"unknown timezone: {timezone_name}") from exc
    local_now = current.astimezone(zone)
    candidate = datetime.combine(
        date=local_now.date(),
        time=time(hour=hour, minute=minute),
        tzinfo=zone,
    )
    if candidate <= local_now:
        candidate += timedelta(days=1)
    return candidate.astimezone(timezone.utc)


def schedule_initial_lower(schedule: dict[str, Any], *, now: datetime) -> datetime:
    checkpoint = _optional_datetime(schedule.get("checkpoint_at"))
    if checkpoint is not None:
        return checkpoint
    cadence = str(schedule.get("cadence") or "24h")
    minutes = int(schedule.get("interval_minutes") or 0)
    if cadence in {"12h", "24h"} and minutes > 0:
        return _utc(now) - timedelta(minutes=minutes)
    return _utc(now) - timedelta(hours=24)


class ScheduleManager:
    """Trigger durable scheduled scans without owning Scan business logic."""

    def __init__(
        self,
        *,
        stream_manager: StreamRunManager,
        config_dir: Path,
        state_dir: Path,
        poll_seconds: float = 60.0,
        webhook: SignedWebhookConfig | None = None,
    ) -> None:
        self.stream_manager = stream_manager
        self.config_dir = config_dir
        self.state_dir = state_dir
        self.poll_seconds = max(1.0, float(poll_seconds))
        self.webhook = webhook if webhook is not None else SignedWebhookConfig.from_env()
        self._task: asyncio.Task[None] | None = None
        self._finalizers: set[asyncio.Task[None]] = set()

    def ledger(self, project_id: str) -> ChangeLedger:
        state = prepare_project_state(
            self.state_dir,
            project_id,
            migrate_legacy_default=True,
        )
        return ChangeLedger(state / "change_ledger.sqlite3")

    def notifications(self, project_id: str) -> NotificationService:
        return NotificationService(
            ledger=self.ledger(project_id),
            project_id=project_id,
            signal_policy=load_signal_policy(self.config_dir / "signal_policy.yaml"),
            webhook=self.webhook,
        )

    def create_schedule(
        self,
        *,
        project_id: str,
        cadence: ScheduleCadence,
        timezone_name: str,
        local_time: str | None,
        mode: RunMode,
        provider_id: str | None,
        max_events: int | None,
        max_events_per_source: int | None,
        now: datetime | None = None,
        intelligence_pipeline: bool = False,
    ) -> dict[str, Any]:
        project_option(project_id, self.config_dir)
        current = _utc(now or datetime.now(timezone.utc))
        next_run = next_schedule_time(
            cadence=cadence,
            now=current,
            timezone_name=timezone_name,
            local_time=local_time,
        )
        interval = {"12h": 720, "24h": 1440}.get(cadence)
        return self.ledger(project_id).create_schedule(
            project_id=project_id,
            cadence=cadence,
            interval_minutes=interval,
            local_time=local_time if cadence == "daily" else None,
            timezone_name=timezone_name,
            mode=mode.value,
            provider_id=provider_id,
            max_events=max_events,
            max_events_per_source=max_events_per_source,
            intelligence_pipeline=intelligence_pipeline,
            next_run_at=next_run,
        )

    def list_schedules(self, *, project_id: str) -> list[dict[str, Any]]:
        project_option(project_id, self.config_dir)
        return self.ledger(project_id).list_schedules(project_id=project_id)

    def disable_schedule(self, *, project_id: str, schedule_id: str) -> bool:
        return self.ledger(project_id).disable_schedule(
            project_id=project_id,
            schedule_id=schedule_id,
        )

    async def start(self) -> None:
        if self._task is not None and not self._task.done():
            return
        await self.reconcile_running()
        self._task = asyncio.create_task(self._loop(), name="signalharness-scheduler")

    async def shutdown(self) -> None:
        if self._task is not None:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)
            self._task = None
        finalizers = list(self._finalizers)
        for task in finalizers:
            task.cancel()
        if finalizers:
            await asyncio.gather(*finalizers, return_exceptions=True)
        self._finalizers.clear()

    async def _loop(self) -> None:
        while True:
            try:
                await self.run_due()
                await self.deliver_due_notifications()
            except asyncio.CancelledError:
                raise
            except Exception:
                # One scheduling iteration must not kill future triggers. Individual
                # run failures are persisted by StreamRunManager and finalize_schedule.
                pass
            await asyncio.sleep(self.poll_seconds)

    async def run_due(self, *, now: datetime | None = None) -> list[str]:
        current = _utc(now or datetime.now(timezone.utc))
        started: list[str] = []
        for project in project_catalog(self.config_dir):
            ledger = self.ledger(project.id)
            for schedule in ledger.due_schedules(project_id=project.id, now=current):
                run_id = f"run-{uuid4().hex[:12]}"
                next_run = next_schedule_time(
                    cadence=_cadence(schedule),
                    now=current,
                    timezone_name=str(schedule["timezone"]),
                    local_time=(
                        str(schedule["local_time"])
                        if schedule.get("local_time") is not None
                        else None
                    ),
                )
                if not ledger.claim_schedule_run(
                    project_id=project.id,
                    schedule_id=str(schedule["schedule_id"]),
                    run_id=run_id,
                    now=current,
                    next_run_at=next_run,
                ):
                    continue
                lower = schedule_initial_lower(schedule, now=current)
                try:
                    session = self.stream_manager.start(
                        source_mode="live",
                        fixture=None,
                        since=lower,
                        until=current,
                        window_mode="custom",
                        mode=RunMode(str(schedule["mode"])),
                        provider_id=(
                            str(schedule["provider_id"])
                            if schedule.get("provider_id") is not None
                            else None
                        ),
                        project=project,
                        max_events=(
                            int(schedule["max_events"])
                            if schedule.get("max_events") is not None
                            else None
                        ),
                        max_events_per_source=(
                            int(schedule["max_events_per_source"])
                            if schedule.get("max_events_per_source") is not None
                            else None
                        ),
                        intelligence_pipeline=bool(schedule.get("intelligence_pipeline", False)),
                        interactive=False,
                        consumer_id=f"schedule:{schedule['schedule_id']}",
                        schedule_id=str(schedule["schedule_id"]),
                        run_id=run_id,
                    )
                except Exception as exc:
                    ledger.complete_schedule_run(
                        project_id=project.id,
                        schedule_id=str(schedule["schedule_id"]),
                        run_id=run_id,
                        status="error",
                        coverage_status="partial",
                        checkpoint_at=None,
                        error=f"{exc.__class__.__name__}: {exc}",
                    )
                    continue
                self.stream_manager.ensure_started(session)
                self._attach_finalizer(project, schedule, session)
                started.append(run_id)
        return started

    async def reconcile_running(self) -> None:
        """Reattach schedule finalizers after StreamRunManager recovery."""

        for project in project_catalog(self.config_dir):
            ledger = self.ledger(project.id)
            for schedule in ledger.list_schedules(project_id=project.id):
                if schedule.get("last_status") != "running" or not schedule.get("last_run_id"):
                    continue
                run_id = str(schedule["last_run_id"])
                session = self.stream_manager.get(run_id)
                if session is not None:
                    self._attach_finalizer(project, schedule, session)
                    continue
                metadata = self.stream_manager.output_dir / "service-runs" / run_id / "service_run.json"
                payload = _read_json(metadata)
                if payload.get("status") in {"success", "error"}:
                    self._complete_from_payload(project, schedule, run_id, payload)
                    if payload.get("status") == "success":
                        await self._notify_scan(project.id, run_id)
                else:
                    ledger.complete_schedule_run(
                        project_id=project.id,
                        schedule_id=str(schedule["schedule_id"]),
                        run_id=run_id,
                        status="error",
                        coverage_status="partial",
                        checkpoint_at=None,
                        error="scheduled run could not be recovered",
                    )

    def _attach_finalizer(
        self,
        project: ProjectOption,
        schedule: dict[str, Any],
        session: StreamRunSession,
    ) -> None:
        task = asyncio.create_task(
            self._finalize(project, schedule, session),
            name=f"signalharness-schedule-finalizer-{session.run_id}",
        )
        self._finalizers.add(task)
        task.add_done_callback(self._finalizers.discard)

    async def _finalize(
        self,
        project: ProjectOption,
        schedule: dict[str, Any],
        session: StreamRunSession,
    ) -> None:
        if session.task is not None:
            await asyncio.gather(session.task, return_exceptions=True)
        payload = session.result or session.public_payload()
        self._complete_from_payload(project, schedule, session.run_id, payload)
        if payload.get("status") == "success":
            await self._notify_scan(project.id, session.run_id)

    async def _notify_scan(self, project_id: str, scan_id: str) -> None:
        service = self.notifications(project_id)
        service.ingest_scan(scan_id=scan_id)
        await service.deliver_due()

    async def deliver_due_notifications(self) -> None:
        if self.webhook is None:
            return
        for project in project_catalog(self.config_dir):
            await self.notifications(project.id).deliver_due()

    def _complete_from_payload(
        self,
        project: ProjectOption,
        schedule: dict[str, Any],
        run_id: str,
        payload: dict[str, Any],
    ) -> None:
        status = str(payload.get("status") or "error")
        coverage = str(payload.get("coverage_status") or "partial")
        window = payload.get("window") if isinstance(payload.get("window"), dict) else {}
        checkpoint = _optional_datetime(window.get("to")) if isinstance(window, dict) else None
        error = str(payload.get("error") or payload.get("error_class") or "")
        if payload.get("intelligence_pipeline") and payload.get("intelligence_status") != "complete":
            checkpoint = None
            coverage = "partial"
            error = error or "Environment interpretation incomplete; checkpoint retained."
        self.ledger(project.id).complete_schedule_run(
            project_id=project.id,
            schedule_id=str(schedule["schedule_id"]),
            run_id=run_id,
            status=status,
            coverage_status=coverage,
            checkpoint_at=checkpoint,
            error=error,
        )


def _cadence(schedule: dict[str, Any]) -> ScheduleCadence:
    raw = str(schedule.get("cadence") or "")
    if raw not in {"12h", "24h", "daily"}:
        raise ValueError(f"invalid persisted cadence: {raw}")
    return cast(ScheduleCadence, raw)


def _parse_local_time(value: str) -> tuple[int, int]:
    parts = value.strip().split(":")
    if len(parts) != 2:
        raise ValueError("local_time must use HH:MM")
    try:
        hour, minute = int(parts[0]), int(parts[1])
    except ValueError as exc:
        raise ValueError("local_time must use HH:MM") from exc
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        raise ValueError("local_time must use a valid 24-hour HH:MM value")
    return hour, minute


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _optional_datetime(value: object) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    parsed = datetime.fromisoformat(value)
    return _utc(parsed)


def _read_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}
