"""Product-only API surface over the shared EnvironmentApplication contract."""

from __future__ import annotations

import asyncio
import json
from datetime import datetime
from typing import Any, AsyncIterator, Literal

from fastapi import APIRouter, Header, HTTPException, Query
from fastapi.sse import EventSourceResponse, ServerSentEvent
from pydantic import BaseModel, ConfigDict, Field, model_validator

from signal_harness.agent_integration.mode import RunMode
from signal_harness.environment_application import EnvironmentApplication, EnvironmentView
from signal_harness.intelligence.deep_dive import (
    DeepDiveManager,
    ProjectActivityDeepDiveUnsupported,
)
from signal_harness.monitoring.scheduler import ScheduleManager
from signal_harness.providers.task_policy import TaskPolicy
from signal_harness.runtime.permissions import SignalPermissionGuard
from signal_harness.signal.policy import load_signal_policy


class EnvironmentScanRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    window: Literal["since_last", "24h", "7d", "30d", "custom"] = "since_last"
    window_from: datetime | None = None
    window_to: datetime | None = None

    @model_validator(mode="after")
    def valid_window(self) -> "EnvironmentScanRequest":
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


class ChangeFeedbackRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    label: Literal["useful", "not_useful", "false_positive", "too_generic"]
    note: str = Field(default="", max_length=2000)


class ChangeOutcomeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    impact_observed: bool | None = None
    action_taken: bool | None = None
    action_helpful: bool | None = None
    resolved: bool | None = None
    note: str = Field(default="", max_length=2000)

    @model_validator(mode="after")
    def require_observed_fact(self) -> "ChangeOutcomeRequest":
        if all(
            value is None
            for value in (
                self.impact_observed,
                self.action_taken,
                self.action_helpful,
                self.resolved,
            )
        ):
            raise ValueError("outcome requires at least one observed boolean fact")
        return self


def intelligence_router(
    *,
    application: EnvironmentApplication,
    deep_dives: DeepDiveManager,
    schedules: ScheduleManager,
) -> APIRouter:
    """Expose the current product domain; protocol adapters do not reconstruct domain truth."""

    router = APIRouter(prefix="/intelligence", tags=["environment intelligence"])
    config_dir = application.config_dir

    @router.get("/meta")
    async def meta() -> dict[str, Any]:
        return application.meta()

    @router.get("/projects/{project_id}")
    async def project_home(project_id: str) -> dict[str, Any]:
        try:
            return application.home(project_id)
        except ValueError as exc:
            raise HTTPException(404, "未找到这个项目") from exc

    @router.post("/projects/{project_id}/scans", status_code=202)
    async def start_scan(project_id: str, request: EnvironmentScanRequest) -> dict[str, Any]:
        try:
            return application.start_scan(
                project_id,
                window=request.window,
                window_from=request.window_from,
                window_to=request.window_to,
            )
        except RuntimeError as exc:
            raise HTTPException(
                409,
                "分析服务尚未配置可用凭据。请在服务端配置模型接口后重试；已有报告仍可阅读。",
            ) from exc
        except ValueError as exc:
            raise HTTPException(400, "时间范围无效，请重新选择") from exc

    @router.delete("/projects/{project_id}/scans/{run_id}")
    async def cancel_scan(project_id: str, run_id: str) -> dict[str, Any]:
        try:
            return await application.cancel_scan(project_id, run_id)
        except KeyError as exc:
            raise HTTPException(404, "没有找到这个项目正在执行的检查任务") from exc
        except RuntimeError as exc:
            raise HTTPException(409, "这次检查已经结束，不能再停止") from exc
        except ValueError as exc:
            raise HTTPException(404, "未找到这个项目") from exc

    @router.get("/projects/{project_id}/reports/{scan_id}")
    async def get_report(project_id: str, scan_id: str) -> dict[str, Any]:
        try:
            return application.require_report(project_id, scan_id)
        except ValueError as exc:
            raise HTTPException(404, str(exc)) from exc

    @router.get("/projects/{project_id}/reports/{scan_id}/activity-summary")
    async def activity_summary(project_id: str, scan_id: str) -> dict[str, Any]:
        try:
            return application.activity_summary(project_id, scan_id)
        except ValueError as exc:
            raise HTTPException(404, str(exc)) from exc

    @router.get("/projects/{project_id}/reports/{scan_id}/changes")
    async def changes(
        project_id: str,
        scan_id: str,
        view: EnvironmentView = "relevant",
        query: str = Query(default="", max_length=400),
        offset: int = Query(default=0, ge=0),
        limit: int = Query(default=25, ge=1, le=100),
        direction_id: str | None = None,
    ) -> dict[str, Any]:
        try:
            return application.changes(
                project_id,
                scan_id,
                view=view,
                query=query,
                offset=offset,
                limit=limit,
                direction_id=direction_id,
            )
        except ValueError as exc:
            raise HTTPException(404, str(exc)) from exc

    @router.get("/projects/{project_id}/reports/{scan_id}/changes/{change_id}")
    async def change_detail(project_id: str, scan_id: str, change_id: str) -> dict[str, Any]:
        try:
            return application.change(project_id, scan_id, change_id)
        except ValueError as exc:
            raise HTTPException(404, str(exc)) from exc

    @router.post(
        "/projects/{project_id}/reports/{scan_id}/changes/{change_id}/feedback",
        status_code=201,
    )
    async def change_feedback(
        project_id: str,
        scan_id: str,
        change_id: str,
        request: ChangeFeedbackRequest,
    ) -> dict[str, Any]:
        try:
            return application.record_feedback(
                project_id,
                scan_id,
                change_id,
                label=request.label,
                note=request.note,
                source="environment-api",
            )
        except PermissionError as exc:
            raise HTTPException(403, "当前项目不允许保存反馈") from exc
        except ValueError as exc:
            raise HTTPException(404, str(exc)) from exc

    @router.post(
        "/projects/{project_id}/reports/{scan_id}/changes/{change_id}/outcome",
        status_code=201,
    )
    async def change_outcome(
        project_id: str,
        scan_id: str,
        change_id: str,
        request: ChangeOutcomeRequest,
    ) -> dict[str, Any]:
        try:
            return application.record_outcome(
                project_id,
                scan_id,
                change_id,
                impact_observed=request.impact_observed,
                action_taken=request.action_taken,
                action_helpful=request.action_helpful,
                resolved=request.resolved,
                note=request.note,
                source="environment-api",
            )
        except PermissionError as exc:
            raise HTTPException(403, "当前项目不允许保存实际结果") from exc
        except ValueError as exc:
            raise HTTPException(404, str(exc)) from exc

    @router.get("/projects/{project_id}/calibration")
    async def calibration_status(
        project_id: str,
        include_episodes: bool = Query(default=False),
    ) -> dict[str, Any]:
        try:
            return application.calibration_status(
                project_id, include_episodes=include_episodes
            )
        except PermissionError as exc:
            raise HTTPException(403, "当前项目不允许读取校准状态") from exc
        except ValueError as exc:
            raise HTTPException(404, str(exc)) from exc

    @router.post(
        "/projects/{project_id}/reports/{scan_id}/changes/{change_id}/deep-dive",
        status_code=202,
    )
    async def start_deep_dive(
        project_id: str,
        scan_id: str,
        change_id: str,
        request: DeepDiveRequest,
    ) -> dict[str, Any]:
        try:
            application.require_report(project_id, scan_id)
            job = await deep_dives.start(project_id, scan_id, change_id, retry=request.retry)
        except ProjectActivityDeepDiveUnsupported as exc:
            raise HTTPException(409, str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(404, "无法找到这条变化的历史版本") from exc
        except PermissionError as exc:
            raise HTTPException(403, "当前项目未授权读取相关上下文") from exc
        return {
            **job,
            "events_url": (
                f"/intelligence/projects/{project_id}/deep-dives/{job['job_id']}/events"
            ),
        }

    @router.get("/projects/{project_id}/deep-dives/{job_id}")
    async def deep_dive_status(project_id: str, job_id: str) -> dict[str, Any]:
        try:
            job = application.repository(project_id).job(job_id, project_id)
        except ValueError as exc:
            raise HTTPException(404, "未找到这个项目") from exc
        if job is None:
            raise HTTPException(404, "未找到核实任务")
        return deep_dives.public(job)

    @router.get(
        "/projects/{project_id}/deep-dives/{job_id}/events",
        response_class=EventSourceResponse,
    )
    async def deep_dive_events(
        project_id: str,
        job_id: str,
        last_event_id: str | None = Header(default=None),
    ) -> AsyncIterator[ServerSentEvent]:
        try:
            repo = application.repository(project_id)
        except ValueError as exc:
            raise HTTPException(404, "未找到这个项目") from exc
        if repo.job(job_id, project_id) is None:
            raise HTTPException(404, "未找到核实任务")
        try:
            cursor = max(0, int(last_event_id or 0))
        except ValueError:
            cursor = 0
        while True:
            job = repo.job(job_id, project_id)
            if job is None:
                return
            if job["sequence"] > cursor:
                cursor = job["sequence"]
                yield ServerSentEvent(
                    data=deep_dives.public(job),
                    event="deep.updated",
                    id=str(cursor),
                    retry=1000,
                )
            if job["status"] in {"complete", "error"}:
                return
            await asyncio.sleep(0.4)

    @router.get("/projects/{project_id}/reports/{scan_id}/audit")
    async def audit(project_id: str, scan_id: str) -> dict[str, Any]:
        try:
            application.require_report(project_id, scan_id)
            repo = application.repository(project_id)
        except ValueError as exc:
            raise HTTPException(404, str(exc)) from exc
        with repo.connect() as db:
            row = db.execute(
                "SELECT audit_json FROM environment_reports WHERE scan_id=?",
                (scan_id,),
            ).fetchone()
        return json.loads(str(row[0])) if row else {}

    @router.get("/projects/{project_id}/reports/{scan_id}/trace")
    async def persisted_trace(project_id: str, scan_id: str) -> list[dict[str, Any]]:
        try:
            return application.trace(project_id, scan_id)
        except ValueError as exc:
            raise HTTPException(404, str(exc)) from exc

    @router.get("/projects/{project_id}/directions/{direction_id}/history")
    async def direction_history(project_id: str, direction_id: str) -> dict[str, Any]:
        try:
            return application.direction_history(project_id, direction_id)
        except ValueError as exc:
            raise HTTPException(404, "未找到这个项目") from exc

    @router.get("/projects/{project_id}/schedules")
    async def schedule_list(project_id: str) -> dict[str, Any]:
        try:
            application.repository(project_id)
        except ValueError as exc:
            raise HTTPException(404, "未找到这个项目") from exc
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
        try:
            application.repository(project_id)
        except ValueError as exc:
            raise HTTPException(404, "未找到这个项目") from exc
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
        return {
            "schedule_id": result["schedule_id"],
            "next_run_at": result["next_run_at"],
        }

    @router.delete("/projects/{project_id}/schedules/{schedule_id}")
    async def disable_schedule(project_id: str, schedule_id: str) -> dict[str, bool]:
        try:
            application.repository(project_id)
        except ValueError as exc:
            raise HTTPException(404, "未找到这个项目") from exc
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
