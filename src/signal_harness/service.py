"""Thin FastAPI service around the existing SignalHarness workflow."""

from __future__ import annotations

import json
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Annotated, Any, AsyncIterator, Literal
from uuid import uuid4

from fastapi import Depends, FastAPI, Header, HTTPException, Query, status
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.sse import EventSourceResponse, ServerSentEvent
from pydantic import BaseModel, ConfigDict, Field, model_validator

from signal_harness.agent_integration.mode import RunMode
from signal_harness.mcp_server import MCP_TOOL_NAMES, build_mcp_server, validate_run_id
from signal_harness.memory import FeedbackMemory
from signal_harness.persistence import ChangeLedger
from signal_harness.providers.catalog import (
    default_provider_id,
    provider_catalog,
    provider_from_selection,
    provider_option,
)
from signal_harness.projects.catalog import (
    default_project_id,
    project_catalog,
    project_option,
)
from signal_harness.projects.state import prepare_project_state
from signal_harness.projects.onboarding import ProjectManifest, draft_project
from signal_harness.resources import (
    is_allowed_fixture_path,
    resolve_config_dir,
    resolve_example_path,
)
from signal_harness.service_streaming import StreamRunManager
from signal_harness.runtime.permissions import SignalPermissionGuard
from signal_harness.runtime.workflow import SignalHarnessWorkflow
from signal_harness.runtime.windows import WindowMode
from signal_harness.signal.feedback import (
    create_feedback_record,
    generate_policy_proposal,
    save_policy_proposal,
)
from signal_harness.signal.policy import load_signal_policy
from signal_harness.signal.schemas import FeedbackLabel
from signal_harness.ui.demo import demo_asset_dir, render_demo_page
from signal_harness.utils.fs import atomic_write_text


class RunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    project_id: str | None = None
    data_source: Literal["live", "fixture"] = "fixture"
    fixture: str | None = "examples/signal_harness/sample_events.json"
    mode: RunMode = RunMode.MOCK_AGENT
    provider_id: str | None = None
    since_days: int = Field(default=14, ge=1, le=30)
    window: Literal["since_last", "24h", "7d", "30d", "custom"] | None = None
    window_from: datetime | None = None
    window_to: datetime | None = None
    max_events: int | None = Field(default=None, ge=1, le=50)
    max_events_per_source: int | None = Field(default=None, ge=1, le=20)

    @model_validator(mode="after")
    def _validate_window(self) -> "RunRequest":
        if self.window == "custom" and self.window_from is None:
            raise ValueError("custom window requires window_from")
        if self.window_from and self.window_to and self.window_from >= self.window_to:
            raise ValueError("window_from must be before window_to")
        return self


class StreamRunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    project_id: str | None = None
    data_source: Literal["live", "fixture"] = "live"
    fixture: str | None = None
    mode: RunMode = RunMode.MOCK_AGENT
    provider_id: str | None = None
    since_days: int = Field(default=14, ge=1, le=30)
    window: Literal["since_last", "24h", "7d", "30d", "custom"] | None = None
    window_from: datetime | None = None
    window_to: datetime | None = None
    max_events: int | None = Field(default=12, ge=1, le=50)
    max_events_per_source: int | None = Field(default=4, ge=1, le=20)

    @model_validator(mode="after")
    def _validate_window(self) -> "StreamRunRequest":
        if self.window == "custom" and self.window_from is None:
            raise ValueError("custom window requires window_from")
        if self.window_from and self.window_to and self.window_from >= self.window_to:
            raise ValueError("window_from must be before window_to")
        return self


class FeedbackRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_id: str
    signal_id: str = Field(min_length=1)
    label: FeedbackLabel
    note: str = ""


class ProjectDraftRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name_hint: str | None = Field(default=None, max_length=120)
    manifests: list[ProjectManifest] = Field(min_length=1, max_length=20)
    paths: list[str] = Field(default_factory=list, max_length=500)

    @model_validator(mode="after")
    def _bounded_manifest_payload(self) -> "ProjectDraftRequest":
        if sum(len(item.content.encode("utf-8")) for item in self.manifests) > 512_000:
            raise ValueError("project draft manifests exceed aggregate size limit")
        return self


@dataclass(frozen=True)
class ServicePaths:
    cwd: Path
    config_dir: Path
    output_dir: Path
    state_dir: Path

    @classmethod
    def resolve(
        cls,
        *,
        cwd: str | Path,
        config_dir: str | Path,
        output_dir: str | Path,
        state_dir: str | Path,
    ) -> "ServicePaths":
        root = Path(cwd).expanduser().resolve()
        return cls(
            cwd=root,
            config_dir=resolve_config_dir(root, config_dir),
            output_dir=_resolve(root, output_dir),
            state_dir=_resolve(root, state_dir),
        )

    def run_output(self, run_id: str) -> Path:
        return self.output_dir / "service-runs" / _validate_run_id(run_id)

    def run_state(self, run_id: str) -> Path:
        return self.state_dir / "service-runs" / _validate_run_id(run_id)

    def project_state(self, project_id: str) -> Path:
        return prepare_project_state(
            self.state_dir,
            project_id,
            migrate_legacy_default=True,
        )


def create_app(
    *,
    cwd: str | Path = Path.cwd(),
    config_dir: str | Path = "configs",
    output_dir: str | Path = "outputs",
    state_dir: str | Path = ".signal-harness",
) -> FastAPI:
    """Create a local API and mount the read-only MCP HTTP transport."""

    paths = ServicePaths.resolve(
        cwd=cwd,
        config_dir=config_dir,
        output_dir=output_dir,
        state_dir=state_dir,
    )
    mcp = build_mcp_server(
        cwd=paths.cwd,
        config_dir=paths.config_dir,
        output_dir=paths.output_dir,
        state_dir=paths.state_dir,
    )

    streams = StreamRunManager(
        cwd=paths.cwd,
        config_dir=paths.config_dir,
        output_dir=paths.output_dir,
        state_dir=paths.state_dir,
    )

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        del app
        async with mcp.session_manager.run():
            await streams.recover_pending()
            try:
                yield
            finally:
                await streams.shutdown()

    app = FastAPI(
        title="SignalHarness API",
        version="0.1.0",
        description="Local API for bounded SignalHarness runs, trace, signals, and feedback.",
        lifespan=lifespan,
    )

    app.mount(
        "/demo-assets",
        StaticFiles(directory=str(demo_asset_dir())),
        name="demo-assets",
    )

    @app.get("/demo", response_class=HTMLResponse)
    async def demo() -> str:
        return render_demo_page()

    @app.get("/demo/meta")
    async def demo_meta() -> dict[str, Any]:
        evidence = _read_json(
            resolve_example_path(paths.cwd, "examples/signal_harness/regression_baseline.json"),
            {},
        )
        providers = provider_catalog(paths.config_dir)
        projects = project_catalog(paths.config_dir)
        return {
            "regression": {
                "suite": evidence.get("suite", "resume-v1"),
                "cases": int(evidence.get("case_count", 40)),
                "passed": bool(evidence.get("passed", False)),
                "decision_accuracy": float(evidence.get("decision_accuracy", 0.0)),
                "category_accuracy": float(evidence.get("category_accuracy", 0.0)),
                "priority_precision": float(evidence.get("priority_precision", 0.0)),
                "priority_recall": float(evidence.get("priority_recall", 0.0)),
                "false_positive_rate": float(evidence.get("false_positive_rate", 0.0)),
                "false_negative_rate": float(evidence.get("false_negative_rate", 0.0)),
            },
            "mcp": {"tool_count": len(MCP_TOOL_NAMES), "tools": list(MCP_TOOL_NAMES)},
            "streaming": {
                "transport": "sse",
                "durability": "persistent-run-retry",
                "event_replay": "in-process",
            },
            "providers": [option.public_payload() for option in providers],
            "default_provider_id": default_provider_id(paths.config_dir),
            "projects": [option.public_payload() for option in projects],
            "default_project_id": default_project_id(paths.config_dir),
        }

    @app.post("/project-drafts")
    async def create_project_draft(request: ProjectDraftRequest) -> dict[str, Any]:
        """Build a review-only project draft from browser-supplied safe manifests."""

        draft = draft_project(
            manifests=request.manifests,
            paths=request.paths,
            name_hint=request.name_hint,
        )
        return draft.model_dump(mode="json")

    @app.post("/stream-runs", status_code=status.HTTP_202_ACCEPTED)
    async def create_stream_run(request: StreamRunRequest) -> dict[str, Any]:
        selected_project_id = request.project_id or default_project_id(paths.config_dir)
        try:
            project = project_option(selected_project_id, paths.config_dir)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="Unknown project selection") from exc

        provider_id = request.provider_id
        if request.mode is RunMode.AGENT:
            provider_id = provider_id or default_provider_id(paths.config_dir)
            if provider_id is None:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail={
                        "code": "agent_provider_not_ready",
                        "reason": "no_configured_provider",
                        "message": "No configured real-model provider is available.",
                    },
                )
            try:
                option = provider_option(provider_id, paths.config_dir)
            except ValueError as exc:
                raise HTTPException(status_code=400, detail="Unknown provider selection") from exc
            if not option.ready:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail={
                        "code": "agent_provider_not_ready",
                        "provider_id": provider_id,
                        "reason": option.reason,
                        "message": f"Provider {provider_id} is not fully configured.",
                    },
                )

        window_mode: WindowMode
        if request.data_source == "fixture":
            fixture = _safe_fixture(
                paths.cwd,
                request.fixture or "examples/signal_harness/sample_events.json",
            )
            since, until, window_mode = None, None, "legacy"
        else:
            fixture = None
            since, until, window_mode = _request_window(request)

        session = streams.start(
            source_mode=request.data_source,
            fixture=fixture,
            since=since,
            until=until,
            window_mode=window_mode,
            mode=request.mode,
            provider_id=provider_id if request.mode is RunMode.AGENT else None,
            project=project,
            max_events=request.max_events,
            max_events_per_source=request.max_events_per_source,
        )
        streams.ensure_started(session)
        return session.public_payload()

    @app.get("/stream-runs/{run_id}")
    async def get_stream_run(run_id: str) -> dict[str, Any]:
        validated = _validate_run_id(run_id)
        session = streams.get(validated)
        if session is None:
            raise HTTPException(status_code=404, detail="Stream run not found")
        return session.public_payload()

    @app.get(
        "/stream-runs/{run_id}/events",
        response_class=EventSourceResponse,
    )
    async def stream_run_events(
        run_id: str,
        cursor: Annotated[int, Depends(_event_cursor)],
    ) -> AsyncIterator[ServerSentEvent]:
        validated = _validate_run_id(run_id)
        session = streams.get(validated)
        if session is None:
            raise HTTPException(status_code=404, detail="Stream run not found")
        async for event in session.subscribe(cursor):
            yield ServerSentEvent(
                data=event.data,
                event=event.event,
                id=str(event.id),
                retry=1000,
            )

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok", "service": "signalharness"}

    @app.post("/runs", status_code=status.HTTP_201_CREATED)
    async def create_run(request: RunRequest) -> dict[str, Any]:
        run_id = f"run-{uuid4().hex[:12]}"
        run_output = paths.run_output(run_id)
        selected_project_id = request.project_id or default_project_id(paths.config_dir)
        try:
            project = project_option(selected_project_id, paths.config_dir)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="Unknown project selection") from exc

        provider_id = request.provider_id
        provider = None
        if request.mode is RunMode.AGENT:
            provider_id = provider_id or default_provider_id(paths.config_dir)
            if provider_id is None:
                raise HTTPException(
                    status_code=409, detail="No configured real-model provider is available"
                )
            try:
                option = provider_option(provider_id, paths.config_dir)
            except ValueError as exc:
                raise HTTPException(status_code=400, detail="Unknown provider selection") from exc
            if not option.ready:
                raise HTTPException(status_code=409, detail=f"Provider {provider_id} is not ready")
            provider = provider_from_selection(provider_id, config_dir=paths.config_dir)

        window_mode: WindowMode
        if request.data_source == "fixture":
            fixture = _safe_fixture(
                paths.cwd, request.fixture or "examples/signal_harness/sample_events.json"
            )
            since, until, window_mode = None, None, "legacy"
        else:
            fixture = None
            since, until, window_mode = _request_window(request)

        workflow = SignalHarnessWorkflow(
            cwd=paths.cwd,
            config_dir=paths.config_dir,
            project_profile_path=project.project_profile_path,
            watchlist_path=project.watchlist_path,
            output_dir=run_output,
            state_dir=paths.project_state(project.id),
            mode=request.mode,
            provider=provider,
            project_id=project.id,
        )
        created_at = datetime.now(timezone.utc).isoformat()
        try:
            async with streams.project_lock(project.id):
                result = await workflow.scan(
                    fixture=fixture,
                    since=since,
                    max_events=request.max_events,
                    max_events_per_source=request.max_events_per_source,
                    scan_id=run_id,
                    window_mode=window_mode,
                    until=until,
                )
        except Exception as exc:
            _write_run_metadata(
                run_output,
                {
                    "run_id": run_id,
                    "status": "error",
                    "mode": request.mode.value,
                    "project_id": project.id,
                    "project_name": project.name,
                    "created_at": created_at,
                    "error_class": exc.__class__.__name__,
                },
            )
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"SignalHarness run failed: {exc.__class__.__name__}",
            ) from exc
        finally:
            if provider is not None:
                await provider.close()

        payload = _run_metadata_payload(
            run_id=run_id,
            mode=request.mode,
            created_at=created_at,
            signals=len(result.signals),
            assessments=len(result.assessments),
            failed_sources=len(result.failed_sources),
        )
        payload["all_changes"] = result.all_change_count
        payload["window"] = result.window.public_payload()
        payload["coverage_status"] = result.coverage_status
        payload["changes_url"] = f"/runs/{run_id}/changes"
        payload["coverage_url"] = f"/runs/{run_id}/coverage"
        payload.update(
            {
                "project_id": project.id,
                "project_name": project.name,
                "source_mode": request.data_source,
                "provider_id": provider_id if request.mode is RunMode.AGENT else None,
                "model": getattr(provider, "model", None),
            }
        )
        _write_run_metadata(run_output, payload)
        return payload

    @app.get("/runs/{run_id}")
    async def get_run(run_id: str) -> dict[str, Any]:
        run_output = _existing_run_output(paths, run_id)
        payload = _read_json(run_output / "service_run.json", None)
        if not isinstance(payload, dict):
            raise HTTPException(status_code=404, detail="Run metadata not found")
        return payload

    @app.get("/runs/{run_id}/trace")
    async def get_run_trace(
        run_id: str,
        limit: int = Query(default=200, ge=1, le=1000),
    ) -> dict[str, Any]:
        run_output = _existing_run_output(paths, run_id)
        payload = _read_json(run_output / "task_trace.json", [])
        items = payload if isinstance(payload, list) else []
        return _collection(items, limit)

    @app.get("/runs/{run_id}/assessments")
    async def get_run_assessments(
        run_id: str,
        limit: int = Query(default=100, ge=1, le=1000),
    ) -> dict[str, Any]:
        run_output = _existing_run_output(paths, run_id)
        payload = _read_json(run_output / "impact_scores.json", [])
        items = payload if isinstance(payload, list) else []
        return _collection(items, limit)

    @app.get("/signals")
    async def get_signals(
        run_id: str,
        limit: int = Query(default=100, ge=1, le=1000),
    ) -> dict[str, Any]:
        run_output = _existing_run_output(paths, run_id)
        payload = _read_json(run_output / "signals.json", [])
        items = payload if isinstance(payload, list) else []
        return _collection(items, limit)

    @app.get("/runs/{run_id}/changes")
    async def get_run_changes(
        run_id: str,
        offset: int = Query(default=0, ge=0),
        limit: int = Query(default=100, ge=1, le=1000),
    ) -> dict[str, Any]:
        run_output = _existing_run_output(paths, run_id)
        run_meta = _read_json(run_output / "service_run.json", {})
        project_id = str(
            run_meta.get("project_id") if isinstance(run_meta, dict) else ""
        ) or default_project_id(paths.config_dir)
        ledger = ChangeLedger(paths.project_state(project_id) / "change_ledger.sqlite3")
        page = ledger.list_scan_changes(run_id, offset=offset, limit=limit)
        return {
            "items": page.items,
            "count": page.count,
            "offset": page.offset,
            "limit": page.limit,
            "returned": len(page.items),
            "has_more": page.has_more,
        }

    @app.get("/runs/{run_id}/coverage")
    async def get_run_coverage(run_id: str) -> dict[str, Any]:
        run_output = _existing_run_output(paths, run_id)
        run_meta = _read_json(run_output / "service_run.json", {})
        project_id = str(
            run_meta.get("project_id") if isinstance(run_meta, dict) else ""
        ) or default_project_id(paths.config_dir)
        ledger = ChangeLedger(paths.project_state(project_id) / "change_ledger.sqlite3")
        items = ledger.source_coverage(run_id)
        statuses = [str(item.get("coverage_status") or "unknown") for item in items]
        overall = (
            "partial" if "partial" in statuses
            else "unknown" if "unknown" in statuses
            else "complete"
        )
        return {"scan_id": run_id, "coverage_status": overall, "sources": items}

    @app.post("/feedback")
    async def save_feedback(request: FeedbackRequest) -> dict[str, Any]:
        run_output = _existing_run_output(paths, request.run_id)
        run_meta = _read_json(run_output / "service_run.json", {})
        project_id = str(
            run_meta.get("project_id") if isinstance(run_meta, dict) else ""
        ) or default_project_id(paths.config_dir)
        project_state = paths.project_state(project_id)
        signals = _read_json(run_output / "signals.json", [])
        assessments = _read_json(run_output / "impact_scores.json", [])
        known_ids = {
            str(item.get("event_id"))
            for collection in (signals, assessments)
            if isinstance(collection, list)
            for item in collection
            if isinstance(item, dict) and item.get("event_id")
        }
        if request.signal_id not in known_ids and request.label is not FeedbackLabel.MISSED_SIGNAL:
            raise HTTPException(status_code=404, detail="Unknown signal_id for this run")
        policy = load_signal_policy(paths.config_dir / "signal_policy.yaml")
        guard = SignalPermissionGuard(policy)
        guard.require("save_feedback")
        guard.require("save_policy_proposal")
        record = create_feedback_record(request.signal_id, request.label, request.note)
        memory = FeedbackMemory(project_state / "feedback_memory.json")
        memory.append(record)
        proposal = generate_policy_proposal(memory.load(), policy)
        save_policy_proposal(project_state / "policy_update_proposal.json", proposal)
        return {
            "run_id": request.run_id,
            "project_id": project_id,
            "signal_id": request.signal_id,
            "feedback": record.feedback.value,
            "proposal_id": proposal.proposal_id,
            "policy_applied": False,
        }

    mcp_app = mcp.streamable_http_app(
        streamable_http_path="/",
        stateless_http=True,
        json_response=True,
    )
    app.mount("/mcp", mcp_app, name="mcp")
    return app


def _request_window(
    request: RunRequest | StreamRunRequest,
) -> tuple[datetime | None, datetime | None, WindowMode]:
    if request.window is None:
        return datetime.now(timezone.utc) - timedelta(days=request.since_days), None, "legacy"
    if request.window == "custom":
        return request.window_from, request.window_to, "custom"
    return None, None, request.window


def _resolve(root: Path, value: str | Path) -> Path:
    target = Path(value).expanduser()
    return target.resolve() if target.is_absolute() else (root / target).resolve()


def _validate_run_id(run_id: str) -> str:
    try:
        return validate_run_id(run_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail="Run not found") from exc


def _existing_run_output(paths: ServicePaths, run_id: str) -> Path:
    run_output = paths.run_output(run_id)
    if not run_output.exists():
        raise HTTPException(status_code=404, detail="Run not found")
    return run_output


def _safe_fixture(root: Path, value: str) -> Path:
    resolved = resolve_example_path(root, value)
    if not is_allowed_fixture_path(resolved, root):
        raise HTTPException(
            status_code=400,
            detail="Fixture must stay inside the project root",
        )
    if resolved.suffix.lower() != ".json" or not resolved.is_file():
        raise HTTPException(status_code=400, detail="Fixture must be an existing JSON file")
    return resolved


def _write_run_metadata(run_output: Path, payload: dict[str, Any]) -> None:
    run_output.mkdir(parents=True, exist_ok=True)
    atomic_write_text(
        run_output / "service_run.json",
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
    )


def _run_metadata_payload(
    *,
    run_id: str,
    mode: RunMode,
    created_at: str,
    signals: int,
    assessments: int,
    failed_sources: int,
) -> dict[str, Any]:
    return {
        "run_id": run_id,
        "status": "success",
        "mode": mode.value,
        "created_at": created_at,
        "signals": signals,
        "assessments": assessments,
        "failed_sources": failed_sources,
        "trace_url": f"/runs/{run_id}/trace",
        "signals_url": f"/signals?run_id={run_id}",
    }


def _read_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return default


def _event_cursor(
    last_event_id: Annotated[str | None, Header()] = None,
) -> int:
    return _parse_event_id(last_event_id)


def _parse_event_id(value: str | None) -> int:
    if value is None or not value.strip():
        return 0
    try:
        parsed = int(value)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid Last-Event-ID") from exc
    if parsed < 0:
        raise HTTPException(status_code=400, detail="Invalid Last-Event-ID")
    return parsed


def _collection(items: list[Any], limit: int) -> dict[str, Any]:
    return {
        "items": items[:limit],
        "count": len(items),
        "returned": min(len(items), limit),
        "truncated": len(items) > limit,
    }
