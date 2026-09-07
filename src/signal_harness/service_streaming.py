"""In-process streaming runs and replayable SSE event history."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal, cast
from uuid import uuid4

from signal_harness.agent_integration.mode import RunMode
from signal_harness.providers.catalog import provider_from_selection
from signal_harness.projects.catalog import ProjectOption, default_project_id, project_option
from signal_harness.projects.state import prepare_project_state
from signal_harness.runtime.tracing import TraceChangeKind
from signal_harness.runtime.workflow import SignalHarnessWorkflow
from signal_harness.runtime.windows import WindowMode
from signal_harness.signal.schemas import SourceTask, TraceStep
from signal_harness.utils.fs import atomic_write_text

StreamRunStatus = Literal["queued", "running", "success", "error"]
StreamSourceMode = Literal["live", "fixture"]
_TERMINAL_EVENTS = {"run.completed", "run.failed"}


@dataclass(frozen=True)
class StreamEvent:
    id: int
    event: str
    data: dict[str, Any]


@dataclass
class StreamRunSession:
    run_id: str
    mode: RunMode
    source_mode: StreamSourceMode
    output_dir: Path
    state_dir: Path
    created_at: str
    fixture: Path | None
    since: datetime | None
    until: datetime | None
    window_mode: WindowMode
    provider_id: str | None
    project: ProjectOption
    max_events: int | None
    max_events_per_source: int | None
    attempt: int = 0
    status: StreamRunStatus = "queued"
    completed_at: str | None = None
    result: dict[str, Any] | None = None
    events: list[StreamEvent] = field(default_factory=list)
    task: asyncio.Task[None] | None = None
    _subscribers: set[asyncio.Queue[StreamEvent]] = field(default_factory=set)

    def publish(self, event: str, data: dict[str, Any]) -> StreamEvent:
        item = StreamEvent(id=len(self.events) + 1, event=event, data=data)
        self.events.append(item)
        for queue in tuple(self._subscribers):
            queue.put_nowait(item)
        return item

    def on_trace_change(
        self,
        kind: TraceChangeKind,
        index: int,
        step: TraceStep,
    ) -> None:
        event = "trace.step" if kind == "append" else "trace.step.updated"
        self.publish(
            event,
            {
                "index": index,
                "operation": kind,
                "trace": step.model_dump(mode="json"),
            },
        )

    async def subscribe(
        self,
        last_event_id: int = 0,
        *,
        on_subscribe: Callable[[], None] | None = None,
    ) -> AsyncIterator[StreamEvent]:
        queue: asyncio.Queue[StreamEvent] = asyncio.Queue()
        self._subscribers.add(queue)
        if on_subscribe is not None:
            on_subscribe()
        try:
            replay = [event for event in self.events if event.id > last_event_id]
            cursor = last_event_id
            for event in replay:
                cursor = event.id
                yield event
                if event.event in _TERMINAL_EVENTS:
                    return
            # Terminal delivery is event-driven. Do not exit only because status flipped:
            # the terminal event may be queued a scheduling tick later.
            while True:
                event = await queue.get()
                if event.id <= cursor:
                    continue
                cursor = event.id
                yield event
                if event.event in _TERMINAL_EVENTS:
                    return
        finally:
            self._subscribers.discard(queue)

    def public_payload(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "status": self.status,
            "mode": self.mode.value,
            "source_mode": self.source_mode,
            "provider_id": self.provider_id,
            "project_id": self.project.id,
            "project_name": self.project.name,
            "since": self.since.isoformat() if self.since else None,
            "until": self.until.isoformat() if self.until else None,
            "window_mode": self.window_mode,
            "created_at": self.created_at,
            "completed_at": self.completed_at,
            "attempt": self.attempt,
            "event_count": len(self.events),
            "events_url": f"/stream-runs/{self.run_id}/events",
            "result_url": f"/runs/{self.run_id}",
        }


class StreamRunManager:
    """Own in-process run tasks plus short-lived SSE replay history."""

    def __init__(
        self,
        *,
        cwd: Path,
        config_dir: Path,
        output_dir: Path,
        state_dir: Path,
        max_sessions: int = 20,
        max_attempts: int = 3,
    ) -> None:
        self.cwd = cwd
        self.config_dir = config_dir
        self.output_dir = output_dir
        self.state_dir = state_dir
        self.max_sessions = max_sessions
        self.max_attempts = max_attempts
        self.sessions: dict[str, StreamRunSession] = {}
        self._project_locks: dict[str, asyncio.Lock] = {}

    def start(
        self,
        *,
        source_mode: StreamSourceMode,
        fixture: Path | None,
        since: datetime | None,
        until: datetime | None = None,
        window_mode: WindowMode = "legacy",
        mode: RunMode,
        provider_id: str | None,
        project: ProjectOption | None = None,
        max_events: int | None = None,
        max_events_per_source: int | None = None,
    ) -> StreamRunSession:
        self._trim_completed()
        selected_project = project or project_option(
            default_project_id(self.config_dir),
            self.config_dir,
        )
        run_id = f"run-{uuid4().hex[:12]}"
        session = StreamRunSession(
            run_id=run_id,
            mode=mode,
            source_mode=source_mode,
            output_dir=self.output_dir / "service-runs" / run_id,
            state_dir=prepare_project_state(
                self.state_dir,
                selected_project.id,
                migrate_legacy_default=True,
            ),
            created_at=datetime.now(timezone.utc).isoformat(),
            fixture=fixture,
            since=since,
            until=until,
            window_mode=window_mode,
            provider_id=provider_id,
            project=selected_project,
            max_events=max_events,
            max_events_per_source=max_events_per_source,
        )
        self.sessions[run_id] = session
        session.publish("run.created", session.public_payload())
        self._persist_session(session)
        return session

    def _session_metadata(self, session: StreamRunSession) -> dict[str, Any]:
        return {
            "run_id": session.run_id,
            "status": session.status,
            "mode": session.mode.value,
            "source_mode": session.source_mode,
            "provider_id": session.provider_id,
            "project_id": session.project.id,
            "project_name": session.project.name,
            "created_at": session.created_at,
            "completed_at": session.completed_at,
            "fixture": str(session.fixture) if session.fixture else None,
            "since": session.since.isoformat() if session.since else None,
            "until": session.until.isoformat() if session.until else None,
            "window_mode": session.window_mode,
            "max_events": session.max_events,
            "max_events_per_source": session.max_events_per_source,
            "attempt": session.attempt,
            "streaming": True,
            "events_url": f"/stream-runs/{session.run_id}/events",
        }

    def _persist_session(self, session: StreamRunSession) -> None:
        _write_run_metadata(session.output_dir, self._session_metadata(session))

    async def recover_pending(self) -> int:
        recovered = 0
        run_root = self.output_dir / "service-runs"
        if not run_root.is_dir():
            return 0
        for metadata_path in sorted(run_root.glob("*/service_run.json")):
            try:
                payload = json.loads(metadata_path.read_text(encoding="utf-8"))
                if not isinstance(payload, dict) or payload.get("status") not in {"queued", "running"}:
                    continue
                run_id = str(payload["run_id"])
                project = project_option(str(payload["project_id"]), self.config_dir)
                mode = RunMode(str(payload["mode"]))
                source_mode = cast(StreamSourceMode, str(payload["source_mode"]))
                window_mode = cast(WindowMode, str(payload.get("window_mode") or "legacy"))
                fixture_raw = payload.get("fixture")
                fixture = Path(str(fixture_raw)).expanduser().resolve() if fixture_raw else None
                since = _optional_datetime(payload.get("since"))
                until = _optional_datetime(payload.get("until"))
                session = StreamRunSession(
                    run_id=run_id, mode=mode, source_mode=source_mode,
                    output_dir=metadata_path.parent,
                    state_dir=prepare_project_state(
                        self.state_dir, project.id, migrate_legacy_default=True
                    ),
                    created_at=str(payload.get("created_at") or datetime.now(timezone.utc).isoformat()),
                    fixture=fixture, since=since, until=until, window_mode=window_mode,
                    provider_id=(str(payload["provider_id"]) if payload.get("provider_id") else None),
                    project=project,
                    max_events=(int(payload["max_events"]) if payload.get("max_events") is not None else None),
                    max_events_per_source=(
                        int(payload["max_events_per_source"])
                        if payload.get("max_events_per_source") is not None else None
                    ),
                    attempt=int(payload.get("attempt") or 0),
                    status="queued",
                )
            except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError):
                continue
            self.sessions[run_id] = session
            session.publish("run.recovered", session.public_payload())
            self._persist_session(session)
            self.ensure_started(session)
            recovered += 1
        return recovered

    def project_lock(self, project_id: str) -> asyncio.Lock:
        return self._project_locks.setdefault(project_id, asyncio.Lock())

    def get(self, run_id: str) -> StreamRunSession | None:
        return self.sessions.get(run_id)

    def ensure_started(self, session: StreamRunSession) -> None:
        if session.task is not None or session.status != "queued":
            return
        if session.attempt >= self.max_attempts:
            session.completed_at = datetime.now(timezone.utc).isoformat()
            session.result = {
                **self._session_metadata(session),
                "status": "error",
                "error_class": "RecoveryAttemptsExhausted",
            }
            session.publish("run.failed", {"run": session.result})
            session.status = "error"
            _write_run_metadata(session.output_dir, session.result)
            return
        session.task = asyncio.create_task(
            self._execute(session),
            name=f"signalharness-stream-{session.run_id}",
        )

    async def shutdown(self) -> None:
        tasks = [
            session.task
            for session in self.sessions.values()
            if session.task is not None and not session.task.done()
        ]
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    async def _execute(self, session: StreamRunSession) -> None:
        session.attempt += 1
        session.status = "running"
        self._persist_session(session)
        session.publish("run.started", session.public_payload())
        provider = None
        try:
            if session.mode is RunMode.AGENT:
                if session.provider_id is None:
                    raise ValueError("agent stream run requires provider_id")
                provider = provider_from_selection(
                    session.provider_id,
                    config_dir=self.config_dir,
                )
            async with self.project_lock(session.project.id):
                workflow = SignalHarnessWorkflow(
                    cwd=self.cwd,
                    config_dir=self.config_dir,
                    project_profile_path=session.project.project_profile_path,
                    watchlist_path=session.project.watchlist_path,
                    output_dir=session.output_dir,
                    state_dir=session.state_dir,
                    mode=session.mode,
                    provider=provider,
                    trace_listener=session.on_trace_change,
                    project_id=session.project.id,
                )
                result = await workflow.scan(
                    fixture=session.fixture,
                    since=session.since,
                    max_events=session.max_events,
                    max_events_per_source=session.max_events_per_source,
                    scan_id=session.run_id,
                    window_mode=session.window_mode,
                    until=session.until,
                )
            session.completed_at = datetime.now(timezone.utc).isoformat()
            source_summary = _source_summary(result.source_tasks, result.failed_sources)
            session.result = {
                "run_id": session.run_id,
                "status": "success",
                "mode": session.mode.value,
                "source_mode": session.source_mode,
                "provider_id": session.provider_id,
                "project_id": session.project.id,
                "project_name": session.project.name,
                "model": getattr(provider, "model", None),
                "created_at": session.created_at,
                "completed_at": session.completed_at,
                "signals": len(result.signals),
                "all_changes": result.all_change_count,
                "window": result.window.public_payload(),
                "coverage_status": result.coverage_status,
                "assessments": len(result.assessments),
                "failed_sources": len(result.failed_sources),
                "source_summary": source_summary,
                "trace_url": f"/runs/{session.run_id}/trace",
                "signals_url": f"/signals?run_id={session.run_id}",
                "assessments_url": f"/runs/{session.run_id}/assessments",
                "changes_url": f"/runs/{session.run_id}/changes",
                "coverage_url": f"/runs/{session.run_id}/coverage",
                "events_url": f"/stream-runs/{session.run_id}/events",
                "streaming": True,
            }
            _write_run_metadata(session.output_dir, session.result)
            session.publish(
                "run.completed",
                {
                    "run": session.result,
                    "signals": [item.model_dump(mode="json") for item in result.signals],
                    "assessments": [item.model_dump(mode="json") for item in result.assessments],
                    "source_tasks": [item.model_dump(mode="json") for item in result.source_tasks],
                    "failed_sources": list(result.failed_sources),
                },
            )
            session.status = "success"
        except Exception as exc:
            session.completed_at = datetime.now(timezone.utc).isoformat()
            session.result = {
                "run_id": session.run_id,
                "status": "error",
                "mode": session.mode.value,
                "source_mode": session.source_mode,
                "provider_id": session.provider_id,
                "project_id": session.project.id,
                "project_name": session.project.name,
                "created_at": session.created_at,
                "completed_at": session.completed_at,
                "error_class": exc.__class__.__name__,
                "events_url": f"/stream-runs/{session.run_id}/events",
                "streaming": True,
            }
            _write_run_metadata(session.output_dir, session.result)
            session.publish("run.failed", {"run": session.result})
            session.status = "error"
        finally:
            if provider is not None:
                await provider.close()

    def _trim_completed(self) -> None:
        if len(self.sessions) < self.max_sessions:
            return
        removable = [
            run_id
            for run_id, session in self.sessions.items()
            if session.status in {"success", "error"}
        ]
        while len(self.sessions) >= self.max_sessions and removable:
            self.sessions.pop(removable.pop(0), None)


def _optional_datetime(value: object) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _source_summary(
    source_tasks: list[SourceTask],
    failed_sources: list[str],
) -> dict[str, int]:
    success = sum(1 for task in source_tasks if task.status == "success")
    partial = sum(1 for task in source_tasks if task.status == "partial_failure")
    failed = sum(1 for task in source_tasks if task.status == "failed")
    return {
        "sources": len(source_tasks),
        "successful_sources": success,
        "partial_sources": partial,
        "failed_sources": max(failed, len(failed_sources)),
        "collected_events": sum(task.output_count for task in source_tasks),
    }


def _write_run_metadata(output_dir: Path, payload: dict[str, Any]) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    atomic_write_text(
        output_dir / "service_run.json",
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
    )
