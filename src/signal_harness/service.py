"""Thin FastAPI service around the existing SignalHarness workflow."""

from __future__ import annotations

import json
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, AsyncIterator
from uuid import uuid4

from fastapi import FastAPI, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict, Field

from signal_harness.agent_integration.mode import RunMode
from signal_harness.mcp_server import build_mcp_server, validate_run_id
from signal_harness.memory import FeedbackMemory
from signal_harness.runtime.permissions import SignalPermissionGuard
from signal_harness.runtime.workflow import SignalHarnessWorkflow
from signal_harness.signal.feedback import (
    create_feedback_record,
    generate_policy_proposal,
    save_policy_proposal,
)
from signal_harness.signal.policy import load_signal_policy
from signal_harness.signal.schemas import FeedbackLabel
from signal_harness.utils.fs import atomic_write_text


class RunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    fixture: str = "examples/signal_harness/sample_events.json"
    mode: RunMode = RunMode.MOCK_AGENT
    max_events: int | None = Field(default=None, ge=1)
    max_events_per_source: int | None = Field(default=None, ge=1)


class FeedbackRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_id: str
    signal_id: str = Field(min_length=1)
    label: FeedbackLabel
    note: str = ""


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
            config_dir=_resolve(root, config_dir),
            output_dir=_resolve(root, output_dir),
            state_dir=_resolve(root, state_dir),
        )

    def run_output(self, run_id: str) -> Path:
        return self.output_dir / "service-runs" / _validate_run_id(run_id)

    def run_state(self, run_id: str) -> Path:
        return self.state_dir / "service-runs" / _validate_run_id(run_id)


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

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        del app
        async with mcp.session_manager.run():
            yield

    app = FastAPI(
        title="SignalHarness API",
        version="0.1.0",
        description="Local API for bounded SignalHarness runs, trace, signals, and feedback.",
        lifespan=lifespan,
    )

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok", "service": "signalharness"}

    @app.post("/runs", status_code=status.HTTP_201_CREATED)
    async def create_run(request: RunRequest) -> dict[str, Any]:
        run_id = f"run-{uuid4().hex[:12]}"
        run_output = paths.run_output(run_id)
        run_state = paths.run_state(run_id)
        fixture = _safe_fixture(paths.cwd, request.fixture)
        workflow = SignalHarnessWorkflow(
            cwd=paths.cwd,
            config_dir=paths.config_dir,
            output_dir=run_output,
            state_dir=run_state,
            mode=request.mode,
        )
        created_at = datetime.now(timezone.utc).isoformat()
        try:
            result = await workflow.scan(
                fixture=fixture,
                max_events=request.max_events,
                max_events_per_source=request.max_events_per_source,
            )
        except Exception as exc:
            _write_run_metadata(
                run_output,
                {
                    "run_id": run_id,
                    "status": "error",
                    "mode": request.mode.value,
                    "created_at": created_at,
                    "error_class": exc.__class__.__name__,
                },
            )
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"SignalHarness run failed: {exc.__class__.__name__}",
            ) from exc

        payload = _run_metadata_payload(
            run_id=run_id,
            mode=request.mode,
            created_at=created_at,
            signals=len(result.signals),
            assessments=len(result.assessments),
            failed_sources=len(result.failed_sources),
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

    @app.get("/signals")
    async def get_signals(
        run_id: str,
        limit: int = Query(default=100, ge=1, le=1000),
    ) -> dict[str, Any]:
        run_output = _existing_run_output(paths, run_id)
        payload = _read_json(run_output / "signals.json", [])
        items = payload if isinstance(payload, list) else []
        return _collection(items, limit)

    @app.post("/feedback")
    async def save_feedback(request: FeedbackRequest) -> dict[str, Any]:
        run_output = _existing_run_output(paths, request.run_id)
        run_state = paths.run_state(request.run_id)
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
        memory = FeedbackMemory(run_state / "feedback_memory.json")
        memory.append(record)
        proposal = generate_policy_proposal(memory.load(), policy)
        save_policy_proposal(run_state / "policy_update_proposal.json", proposal)
        return {
            "run_id": request.run_id,
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
    target = Path(value).expanduser()
    resolved = target.resolve() if target.is_absolute() else (root / target).resolve()
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Fixture must stay inside the project root") from exc
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


def _collection(items: list[Any], limit: int) -> dict[str, Any]:
    return {
        "items": items[:limit],
        "count": len(items),
        "returned": min(len(items), limit),
        "truncated": len(items) > limit,
    }
