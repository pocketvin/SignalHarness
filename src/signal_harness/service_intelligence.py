"""Product-only API surface: no mock, model, scoring, or fixed Top-K request fields."""

from __future__ import annotations

import asyncio
import json
from datetime import datetime
from pathlib import Path
from typing import Any, AsyncIterator, Literal

from fastapi import APIRouter, Header, HTTPException, Query
from fastapi.sse import EventSourceResponse, ServerSentEvent
from pydantic import BaseModel, ConfigDict, model_validator
from signal_harness.monitoring.scheduler import ScheduleManager
from signal_harness.runtime.permissions import SignalPermissionGuard
from signal_harness.signal.policy import load_signal_policy

from signal_harness.agent_integration.mode import RunMode
from signal_harness.intelligence.deep_dive import DeepDiveManager
from signal_harness.persistence.intelligence import IntelligenceRepository
from signal_harness.projects.catalog import default_project_id, project_catalog, project_option
from signal_harness.providers.task_policy import TaskPolicy
from signal_harness.service_streaming import StreamRunManager


class EnvironmentScanRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    window: Literal["since_last", "24h", "7d", "30d", "custom"] = "since_last"
    window_from: datetime | None = None
    window_to: datetime | None = None

    @model_validator(mode="after")
    def valid_window(self) -> EnvironmentScanRequest:
        if self.window == "custom":
            if self.window_from is None:
                raise ValueError("自定义范围需要开始时间")
            if self.window_to and self.window_from >= self.window_to:
                raise ValueError("开始时间必须早于结束时间")
        elif self.window_from or self.window_to:
            raise ValueError("固定时间窗口不接受额外日期")
        return self


class EnvironmentScheduleRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    cadence: Literal["12h", "24h", "daily"] = "24h"
    timezone: str = "Asia/Shanghai"
    local_time: str | None = None


class DeepDiveRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    retry: bool = False


def intelligence_router(
    *,
    config_dir: Path,
    streams: StreamRunManager,
    deep_dives: DeepDiveManager,
    schedules: ScheduleManager,
) -> APIRouter:
    router = APIRouter(prefix="/intelligence", tags=["environment intelligence"])

    def repository(project_id: str) -> IntelligenceRepository:
        try:
            return deep_dives.repository(project_id)
        except ValueError as exc:
            raise HTTPException(404, "未找到这个项目") from exc

    def report(project_id: str, scan_id: str) -> dict[str, Any]:
        value = repository(project_id).report(scan_id)
        if not value or value.get("project_id") != project_id:
            raise HTTPException(404, "该项目下没有这份环境报告")
        return value

    def active(project_id: str) -> dict[str, Any] | None:
        for session in reversed(list(streams.sessions.values())):
            if (
                session.project.id == project_id
                and session.intelligence_pipeline
                and session.status in {"queued", "running"}
            ):
                return {
                    "run_id": session.run_id,
                    "status": session.status,
                    "progress": session.progress,
                    "events_url": f"/stream-runs/{session.run_id}/events",
                }
        return None

    @router.get("/meta")
    async def meta() -> dict[str, Any]:
        policy = TaskPolicy.load(config_dir)
        return {
            "projects": [item.public_payload() for item in project_catalog(config_dir)],
            "default_project_id": default_project_id(config_dir),
            "intelligence_ready": bool(
                policy.providers("shallow") and policy.providers("synthesis")
            ),
        }

    @router.get("/projects/{project_id}")
    async def project_home(project_id: str) -> dict[str, Any]:
        repo = repository(project_id)
        return {
            "report": repo.latest_report(project_id),
            "history": repo.reports(project_id),
            "active_run": active(project_id),
        }

    @router.post("/projects/{project_id}/scans", status_code=202)
    async def start_scan(project_id: str, request: EnvironmentScanRequest) -> dict[str, Any]:
        repository(project_id)
        running = active(project_id)
        if running:
            return running
        policy = TaskPolicy.load(config_dir)
        if not policy.providers("shallow") or not policy.providers("synthesis"):
            raise HTTPException(
                409, "分析服务尚未配置可用凭据。请在服务端配置模型接口后重试；已有报告仍可阅读。"
            )
        project = project_option(project_id, config_dir)
        try:
            session = streams.start(
                source_mode="live",
                fixture=None,
                since=request.window_from,
                until=request.window_to,
                window_mode=request.window,
                mode=RunMode.AGENT,
                provider_id=None,
                project=project,
                max_events=None,
                max_events_per_source=None,
                intelligence_pipeline=True,
            )
        except ValueError as exc:
            raise HTTPException(400, "时间范围无效，请重新选择") from exc
        streams.ensure_started(session)
        return {
            "run_id": session.run_id,
            "status": session.status,
            "progress": session.progress,
            "window": session.frozen_window,
            "events_url": f"/stream-runs/{session.run_id}/events",
        }

    @router.get("/projects/{project_id}/reports/{scan_id}")
    async def get_report(project_id: str, scan_id: str) -> dict[str, Any]:
        return report(project_id, scan_id)

    @router.get("/projects/{project_id}/reports/{scan_id}/changes")
    async def changes(
        project_id: str,
        scan_id: str,
        view: Literal["all", "relevant", "featured", "unavailable"] = "relevant",
        query: str = Query(default="", max_length=400),
        offset: int = Query(default=0, ge=0),
        limit: int = Query(default=25, ge=1, le=100),
        direction_id: str | None = None,
    ) -> dict[str, Any]:
        saved = report(project_id, scan_id)
        ids = None
        if direction_id:
            direction = next(
                (d for d in saved["directions"] if d["direction_id"] == direction_id), None
            )
            if direction is None:
                raise HTTPException(404, "该报告中未找到这个方向")
            ids = list(
                dict.fromkeys(
                    direction["supporting_change_ids"] + direction["contradicting_change_ids"]
                )
            )
            view = "all"  # Direction evidence includes context-only and counter evidence.
        return repository(project_id).changes(
            scan_id, view=view, query=query, offset=offset, limit=limit, ids=ids
        )

    @router.get("/projects/{project_id}/reports/{scan_id}/changes/{change_id}")
    async def change_detail(project_id: str, scan_id: str, change_id: str) -> dict[str, Any]:
        report(project_id, scan_id)
        item = repository(project_id).change(scan_id, change_id)
        if item is None:
            raise HTTPException(404, "这份报告中没有该变化")
        return item

    @router.post(
        "/projects/{project_id}/reports/{scan_id}/changes/{change_id}/deep-dive", status_code=202
    )
    async def start_deep_dive(
        project_id: str, scan_id: str, change_id: str, request: DeepDiveRequest
    ) -> dict[str, Any]:
        report(project_id, scan_id)
        try:
            job = await deep_dives.start(project_id, scan_id, change_id, retry=request.retry)
        except ValueError as exc:
            raise HTTPException(404, "无法找到这条变化的历史版本") from exc
        except PermissionError as exc:
            raise HTTPException(403, "当前项目未授权读取相关上下文") from exc
        return {
            **job,
            "events_url": f"/intelligence/projects/{project_id}/deep-dives/{job['job_id']}/events",
        }

    @router.get("/projects/{project_id}/deep-dives/{job_id}")
    async def deep_dive_status(project_id: str, job_id: str) -> dict[str, Any]:
        job = repository(project_id).job(job_id, project_id)
        if job is None:
            raise HTTPException(404, "未找到核实任务")
        return deep_dives.public(job)

    @router.get(
        "/projects/{project_id}/deep-dives/{job_id}/events", response_class=EventSourceResponse
    )
    async def deep_dive_events(
        project_id: str,
        job_id: str,
        last_event_id: str | None = Header(default=None),
    ) -> AsyncIterator[ServerSentEvent]:
        repo = repository(project_id)
        if repo.job(job_id, project_id) is None:
            raise HTTPException(404, "未找到核实任务")
        try:
            cursor = max(0, int(last_event_id or 0))
        except ValueError:
            cursor = 0
        # Durable latest-state replay survives tab refresh and reconnection. Intermediate
        # progress is coalesced, but a completed result is never represented as partial text.
        while True:
            job = repo.job(job_id, project_id)
            if job is None:
                return
            if job["sequence"] > cursor:
                cursor = job["sequence"]
                yield ServerSentEvent(
                    data=deep_dives.public(job), event="deep.updated", id=str(cursor), retry=1000
                )
            if job["status"] in {"complete", "error"}:
                return
            await asyncio.sleep(0.4)

    @router.get("/projects/{project_id}/reports/{scan_id}/audit")
    async def audit(project_id: str, scan_id: str) -> dict[str, Any]:
        report(project_id, scan_id)
        repo = repository(project_id)
        with repo.connect() as db:
            row = db.execute(
                "SELECT audit_json FROM environment_reports WHERE scan_id=?", (scan_id,)
            ).fetchone()
        return json.loads(row[0]) if row else {}

    @router.get("/projects/{project_id}/directions/{direction_id}/history")
    async def direction_history(project_id: str, direction_id: str) -> dict[str, Any]:
        with repository(project_id).connect() as db:
            rows = db.execute(
                "SELECT payload_json,created_at FROM environment_direction_revisions "
                "WHERE project_id=? AND direction_id=? ORDER BY created_at DESC LIMIT 20",
                (project_id, direction_id),
            ).fetchall()
        return {"items": [{**json.loads(row[0]), "created_at": row[1]} for row in rows]}

    @router.get("/projects/{project_id}/schedules")
    async def schedule_list(project_id: str) -> dict[str, Any]:
        repository(project_id)
        items = schedules.ledger(project_id).list_schedules(project_id=project_id)
        fields = {
            "schedule_id",
            "cadence",
            "local_time",
            "timezone",
            "enabled",
            "next_run_at",
            "last_status",
            "checkpoint_at",
        }
        return {
            "items": [
                {key: value for key, value in item.items() if key in fields}
                for item in items
                if item.get("intelligence_pipeline")
            ]
        }

    @router.post("/projects/{project_id}/schedules", status_code=201)
    async def create_schedule(
        project_id: str, request: EnvironmentScheduleRequest
    ) -> dict[str, Any]:
        repository(project_id)
        SignalPermissionGuard(load_signal_policy(config_dir / "signal_policy.yaml")).require(
            "manage_schedules", confirmed=True
        )
        if not TaskPolicy.load(config_dir).providers("synthesis"):
            raise HTTPException(409, "请先在服务端配置分析接口")
        try:
            result = schedules.create_schedule(
                project_id=project_id,
                cadence=request.cadence,
                timezone_name=request.timezone,
                local_time=request.local_time,
                mode=RunMode.AGENT,
                provider_id=None,
                max_events=None,
                max_events_per_source=None,
                intelligence_pipeline=True,
            )
        except ValueError as exc:
            raise HTTPException(400, "频率、时间或时区设置无效") from exc
        return {"schedule_id": result["schedule_id"], "next_run_at": result["next_run_at"]}

    @router.delete("/projects/{project_id}/schedules/{schedule_id}")
    async def disable_schedule(project_id: str, schedule_id: str) -> dict[str, bool]:
        repository(project_id)
        SignalPermissionGuard(load_signal_policy(config_dir / "signal_policy.yaml")).require(
            "manage_schedules", confirmed=True
        )
        result = schedules.ledger(project_id).disable_schedule(
            project_id=project_id, schedule_id=schedule_id
        )
        if not result:
            raise HTTPException(404, "未找到该计划")
        return {"disabled": True}

    return router
