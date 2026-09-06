"""In-process streaming runs and replayable SSE event history."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

from signal_harness.agent_integration.mode import RunMode
from signal_harness.providers.catalog import provider_from_selection
from signal_harness.projects.catalog import ProjectOption, default_project_id, project_option
from signal_harness.runtime.tracing import TraceChangeKind
from signal_harness.runtime.workflow import SignalHarnessWorkflow
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
    provider_id: str | None
    project: ProjectOption
    max_events: int | None
    max_events_per_source: int | None
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
            if self.status in {"success", "error"}:
                return
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
            "created_at": self.created_at,
            "completed_at": self.completed_at,
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
    ) -> None:
        self.cwd = cwd
        self.config_dir = config_dir
        self.output_dir = output_dir
        self.state_dir = state_dir
        self.max_sessions = max_sessions
        self.sessions: dict[str, StreamRunSession] = {}

    def start(
        self,
        *,
        source_mode: StreamSourceMode,
        fixture: Path | None,
        since: datetime | None,
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
            state_dir=self.state_dir / "service-runs" / run_id,
            created_at=datetime.now(timezone.utc).isoformat(),
            fixture=fixture,
            since=since,
            provider_id=provider_id,
            project=selected_project,
            max_events=max_events,
            max_events_per_source=max_events_per_source,
        )
        self.sessions[run_id] = session
        session.publish("run.created", session.public_payload())
        return session

    def get(self, run_id: str) -> StreamRunSession | None:
        return self.sessions.get(run_id)

    def ensure_started(self, session: StreamRunSession) -> None:
        if session.task is not None or session.status != "queued":
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
        session.status = "running"
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
            )
            result = await workflow.scan(
                fixture=session.fixture,
                since=session.since,
                max_events=session.max_events,
                max_events_per_source=session.max_events_per_source,
            )
            session.status = "success"
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
                "assessments": len(result.assessments),
                "failed_sources": len(result.failed_sources),
                "source_summary": source_summary,
                "trace_url": f"/runs/{session.run_id}/trace",
                "signals_url": f"/signals?run_id={session.run_id}",
                "assessments_url": f"/runs/{session.run_id}/assessments",
                "events_url": f"/stream-runs/{session.run_id}/events",
                "streaming": True,
            }
            _write_run_metadata(session.output_dir, session.result)
            session.publish(
                "run.completed",
                {
                    "run": session.result,
                    "signals": [item.model_dump(mode="json") for item in result.signals],
                    "assessments": [
                        item.model_dump(mode="json") for item in result.assessments
                    ],
                    "source_tasks": [
                        item.model_dump(mode="json") for item in result.source_tasks
                    ],
                    "failed_sources": list(result.failed_sources),
                },
            )
        except Exception as exc:
            session.status = "error"
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
