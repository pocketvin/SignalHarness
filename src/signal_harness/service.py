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

from signal_harness.intelligence.deep_dive import DeepDiveManager
from signal_harness.service_intelligence import intelligence_router
from signal_harness.agent_integration.mode import RunMode
from signal_harness.calibration import build_calibration_dataset, evaluate_calibration_replay
from signal_harness.capability_eval import load_capability_suite
from signal_harness.mcp_server import (
    MCP_TOOL_NAMES,
    MCP_WRITE_TOOL_NAMES,
    build_mcp_server,
    validate_run_id,
)
from signal_harness.memory import FeedbackMemory
from signal_harness.monitoring import ScheduleManager
from signal_harness.narrative_calibration import (
    DimensionPreference,
    HumanPreference,
    load_review_file,
    narrative_calibration_status,
    update_narrative_review_pair,
)
from signal_harness.persistence import ChangeLedger
from signal_harness.product_intelligence import ImpactGroup, ProductIntelligenceService
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
from signal_harness.projects.github_onboarding import draft_github_project
from signal_harness.projects.onboarding import (
    ProjectManifest,
    apply_project_draft,
    draft_project,
)
from signal_harness.projects.preferences import (
    Importance,
    PreferenceInput,
    PreferenceScope,
    parse_preference_instruction,
)
from signal_harness.resources import (
    is_allowed_fixture_path,
    resolve_config_dir,
    resolve_example_path,
)
from signal_harness.service_streaming import StreamRunManager
from signal_harness.golden_candidates import record_feedback_candidate
from signal_harness.runtime.permissions import SignalPermissionGuard
from signal_harness.runtime.workflow import SignalHarnessWorkflow
from signal_harness.runtime.windows import WindowMode
from signal_harness.signal.feedback import (
    create_feedback_record,
    generate_policy_proposal,
    save_policy_proposal,
)
from signal_harness.signal.policy import load_signal_policy, load_yaml_mapping
from signal_harness.signal.schemas import FeedbackLabel
from signal_harness.ui.demo import (
    demo_asset_dir,
    render_demo_page,
    render_narrative_review_page,
)
from signal_harness.utils.fs import atomic_write_text


class RunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    project_id: str | None = None
    data_source: Literal["live", "fixture"] = "fixture"
    fixture: str | None = "examples/signal_harness/sample_events.json"
    mode: RunMode = RunMode.MOCK_AGENT
    provider_id: str | None = None
    since_days: int = Field(default=14, ge=1, le=3650)
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
    since_days: int = Field(default=14, ge=1, le=3650)
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


class ScheduleRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    cadence: Literal["12h", "24h", "daily"] = "24h"
    timezone: str = Field(default="UTC", min_length=1, max_length=80)
    local_time: str | None = Field(default=None, max_length=5)
    mode: RunMode = RunMode.MOCK_AGENT
    provider_id: str | None = None
    max_events: int | None = Field(default=12, ge=1, le=50)
    max_events_per_source: int | None = Field(default=4, ge=1, le=20)

    @model_validator(mode="after")
    def _validate_cadence(self) -> "ScheduleRequest":
        if self.cadence == "daily" and not self.local_time:
            raise ValueError("daily schedule requires local_time in HH:MM")
        if self.cadence != "daily" and self.local_time is not None:
            raise ValueError("local_time is only valid for daily schedules")
        return self


class OutcomeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scan_id: str = Field(min_length=1, max_length=120)
    change_id: str = Field(min_length=1, max_length=120)
    impact_observed: bool | None = None
    action_taken: bool | None = None
    action_helpful: bool | None = None
    resolved: bool | None = None
    note: str = Field(default="", max_length=2000)

    @model_validator(mode="after")
    def _require_observed_fact(self) -> "OutcomeRequest":
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


class FeedbackRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_id: str
    signal_id: str = Field(min_length=1)
    label: FeedbackLabel
    note: str = ""


class NarrativeReviewUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    human_preference: HumanPreference
    dimension_preferences: dict[str, DimensionPreference]
    human_note: str = Field(default="", max_length=2000)


class PreferenceRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scope_type: PreferenceScope
    scope_key: str = Field(min_length=1, max_length=160)
    importance: Importance
    note: str = Field(default="", max_length=1000)


class NaturalLanguagePreferenceRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    instruction: str = Field(min_length=1, max_length=1000)


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


class ProjectConnectRequest(ProjectDraftRequest):
    replace_existing: bool = False


class GitHubProjectConnectRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    url: str = Field(min_length=1, max_length=500)
    replace_existing: bool = False


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


def _narrative_review_path(paths: ServicePaths) -> Path:
    return paths.output_dir / "narrative-calibration" / "narrative_pairs.review.json"


def _narrative_review_payload(paths: ServicePaths) -> dict[str, Any]:
    review_path = _narrative_review_path(paths)
    if not review_path.is_file():
        raise HTTPException(
            status_code=404,
            detail=(
                "Narrative review data is not prepared. Export a valid blind review to "
                "outputs/narrative-calibration first."
            ),
        )
    review = load_review_file(review_path)
    suite = load_capability_suite(
        resolve_example_path(
            paths.cwd, Path("examples/signal_harness/capability_golden_v1.json")
        )
    )
    case_by_id = {case.id: case for case in suite.cases}

    def output_payload(output: Any) -> dict[str, Any]:
        # Decision/score are intentionally omitted from the reviewer API to avoid anchoring.
        return {
            "what_changed_zh": output.what_changed_zh,
            "why_relevant_zh": output.why_relevant_zh,
            "recommended_actions_zh": list(output.recommended_actions_zh),
        }

    pairs: list[dict[str, Any]] = []
    for pair in review.pairs:
        case = case_by_id.get(pair.case_id)
        if case is None:
            continue
        pairs.append(
            {
                "pair_id": pair.pair_id,
                "case_id": pair.case_id,
                "case_title": pair.case_title,
                "truth_status": pair.truth_status,
                "context": {
                    "source_type": case.event.source_type,
                    "source_name": case.event.source_name,
                    "event_title": case.event.title,
                    "event_content": case.event.content,
                    "evidence_summary": case.evidence.context_summary,
                    "evidence_uncertainty": case.evidence.uncertainty,
                    "unsupported_claims": list(case.evidence.unsupported_claims),
                },
                "A": output_payload(pair.A),
                "B": output_payload(pair.B),
                "human_preference": pair.human_preference,
                "dimension_preferences": dict(pair.dimension_preferences),
                "human_note": pair.human_note,
            }
        )
    return {
        "version": review.version,
        "source_provider": review.source_provider,
        "source_model": review.source_model,
        "calibration_eligible": review.calibration_eligible,
        "rubric_dimensions": list(review.rubric_dimensions),
        "minimum_labeled_pairs": review.minimum_labeled_pairs,
        "status": narrative_calibration_status(review).model_dump(mode="json"),
        "pairs": pairs,
    }


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
    streams = StreamRunManager(
        cwd=paths.cwd,
        config_dir=paths.config_dir,
        output_dir=paths.output_dir,
        state_dir=paths.state_dir,
    )
    deep_dives = DeepDiveManager(paths.config_dir, paths.state_dir)
    schedules = ScheduleManager(
        stream_manager=streams,
        config_dir=paths.config_dir,
        state_dir=paths.state_dir,
    )
    mcp = build_mcp_server(
        cwd=paths.cwd,
        config_dir=paths.config_dir,
        output_dir=paths.output_dir,
        state_dir=paths.state_dir,
        stream_manager=streams,
    )

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        del app
        async with mcp.session_manager.run():
            await deep_dives.startup()
            await streams.recover_pending()
            await schedules.start()
            try:
                yield
            finally:
                await deep_dives.shutdown()
                await schedules.shutdown()
                await streams.shutdown()

    app = FastAPI(
        title="SignalHarness API",
        version="0.1.0",
        description="Local API for bounded SignalHarness runs, trace, signals, and feedback.",
        lifespan=lifespan,
    )
    app.include_router(intelligence_router(config_dir=paths.config_dir, streams=streams, deep_dives=deep_dives, schedules=schedules))
    app.state.deep_dive_manager = deep_dives
    app.state.stream_manager = streams
    app.state.schedule_manager = schedules

    app.mount(
        "/demo-assets",
        StaticFiles(directory=str(demo_asset_dir())),
        name="demo-assets",
    )

    @app.get("/demo", response_class=HTMLResponse)
    async def demo() -> str:
        return render_demo_page()

    @app.get("/eval/narrative", response_class=HTMLResponse)
    async def narrative_review_page() -> str:
        return render_narrative_review_page()

    @app.get("/eval/narrative/data")
    async def narrative_review_data() -> dict[str, Any]:
        return _narrative_review_payload(paths)

    @app.post("/eval/narrative/pairs/{pair_id}")
    async def save_narrative_review(
        pair_id: str, request: NarrativeReviewUpdateRequest
    ) -> dict[str, Any]:
        review_path = _narrative_review_path(paths)
        if not review_path.is_file():
            raise HTTPException(status_code=404, detail="Narrative review data is not prepared")
        try:
            review = update_narrative_review_pair(
                review_path,
                pair_id=pair_id,
                human_preference=request.human_preference,
                dimension_preferences=request.dimension_preferences,
                human_note=request.human_note,
            )
        except ValueError as exc:
            message = str(exc)
            raise HTTPException(
                status_code=404 if message.startswith("unknown narrative pair") else 400,
                detail=message,
            ) from exc
        pair = next(item for item in review.pairs if item.pair_id == pair_id)
        return {
            "pair_id": pair_id,
            "saved": True,
            "human_preference": pair.human_preference,
            "dimension_preferences": pair.dimension_preferences,
            "human_note": pair.human_note,
            "status": narrative_calibration_status(review).model_dump(mode="json"),
        }

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
            "mcp": {
                "tool_count": len(MCP_TOOL_NAMES),
                "read_only_tool_count": len(MCP_TOOL_NAMES) - len(MCP_WRITE_TOOL_NAMES),
                "write_tool_count": len(MCP_WRITE_TOOL_NAMES),
                "tools": list(MCP_TOOL_NAMES),
            },
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

    def project_profile_snapshot(project_id: str) -> tuple[dict[str, Any], ChangeLedger]:
        try:
            option = project_option(project_id, paths.config_dir)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail="Project not found") from exc
        auto_profile = load_yaml_mapping(option.project_profile_path)
        ledger = ChangeLedger(paths.project_state(project_id) / "change_ledger.sqlite3")
        return ledger.ensure_profile_revision(
            project_id=project_id, auto_profile=auto_profile
        ), ledger

    def require_profile_write_permission() -> None:
        policy = load_signal_policy(paths.config_dir / "signal_policy.yaml")
        guard = SignalPermissionGuard(policy)
        guard.require("modify_project_profile", confirmed=True)

    def product_service_for_run(run_id: str) -> ProductIntelligenceService:
        run_output = _existing_run_output(paths, run_id)
        run_meta = _read_json(run_output / "service_run.json", {})
        project_id = str(
            run_meta.get("project_id") if isinstance(run_meta, dict) else ""
        ) or default_project_id(paths.config_dir)
        return ProductIntelligenceService(
            ledger=ChangeLedger(paths.project_state(project_id) / "change_ledger.sqlite3"),
            project_id=project_id,
        )

    @app.get("/projects/{project_id}/profile")
    async def get_project_profile(project_id: str) -> dict[str, Any]:
        snapshot, _ = project_profile_snapshot(project_id)
        return snapshot

    @app.post("/projects/{project_id}/preferences")
    async def set_project_preference(
        project_id: str, request: PreferenceRequest
    ) -> dict[str, Any]:
        snapshot, ledger = project_profile_snapshot(project_id)
        require_profile_write_permission()
        ledger.set_preference(
            project_id=project_id,
            preference=PreferenceInput(
                scope_type=request.scope_type,
                scope_key=request.scope_key,
                importance=request.importance,
                source="ui",
                note=request.note,
            ),
        )
        return ledger.ensure_profile_revision(
            project_id=project_id, auto_profile=snapshot["auto_profile"]
        )

    @app.post("/projects/{project_id}/preferences/natural-language")
    async def set_project_preference_natural_language(
        project_id: str, request: NaturalLanguagePreferenceRequest
    ) -> dict[str, Any]:
        snapshot, ledger = project_profile_snapshot(project_id)
        require_profile_write_permission()
        preference = parse_preference_instruction(
            request.instruction, snapshot["effective_profile"]
        )
        ledger.set_preference(project_id=project_id, preference=preference)
        return ledger.ensure_profile_revision(
            project_id=project_id, auto_profile=snapshot["auto_profile"]
        )

    @app.delete("/projects/{project_id}/preferences/{preference_id}")
    async def revoke_project_preference(
        project_id: str, preference_id: str
    ) -> dict[str, Any]:
        snapshot, ledger = project_profile_snapshot(project_id)
        require_profile_write_permission()
        if not ledger.revoke_preference(
            project_id=project_id, preference_id=preference_id
        ):
            raise HTTPException(status_code=404, detail="Active preference not found")
        return ledger.ensure_profile_revision(
            project_id=project_id, auto_profile=snapshot["auto_profile"]
        )

    @app.post("/project-drafts")
    async def create_project_draft(request: ProjectDraftRequest) -> dict[str, Any]:
        """Build a safe auto-active-capable project preview from browser manifests."""

        draft = draft_project(
            manifests=request.manifests,
            paths=request.paths,
            name_hint=request.name_hint,
        )
        return draft.model_dump(mode="json")

    @app.post("/projects/connect", status_code=status.HTTP_201_CREATED)
    async def connect_project(request: ProjectConnectRequest) -> dict[str, Any]:
        draft = draft_project(
            manifests=request.manifests,
            paths=request.paths,
            name_hint=request.name_hint,
        )
        policy = load_signal_policy(paths.config_dir / "signal_policy.yaml")
        guard = SignalPermissionGuard(policy)
        guard.require("modify_project_profile", confirmed=True)
        guard.require("add_watchlist_source", confirmed=True)
        try:
            applied = apply_project_draft(
                draft, paths.config_dir, overwrite=request.replace_existing
            )
        except FileExistsError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        snapshot, _ = project_profile_snapshot(draft.id)
        option = project_option(draft.id, paths.config_dir)
        return {
            "auto_active": True,
            "project": option.public_payload(),
            "profile": snapshot,
            "applied_files": {key: str(value) for key, value in applied.items()},
        }

    @app.post("/projects/connect/github", status_code=status.HTTP_201_CREATED)
    async def connect_github_project(request: GitHubProjectConnectRequest) -> dict[str, Any]:
        policy = load_signal_policy(paths.config_dir / "signal_policy.yaml")
        guard = SignalPermissionGuard(policy)
        guard.require("modify_project_profile", confirmed=True)
        guard.require("add_watchlist_source", confirmed=True)
        try:
            draft = await draft_github_project(request.url)
            try:
                applied = apply_project_draft(
                    draft, paths.config_dir, overwrite=request.replace_existing
                )
                already_connected = False
            except FileExistsError:
                # Importing the same repository again is a selection action, not an error.
                # Keep the existing reviewed config unless replace_existing was explicit.
                project_option(draft.id, paths.config_dir)
                applied = {}
                already_connected = True
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        snapshot, _ = project_profile_snapshot(draft.id)
        option = project_option(draft.id, paths.config_dir)
        return {
            "auto_active": True,
            "source": "github",
            "already_connected": already_connected,
            "project": option.public_payload(),
            "profile": snapshot,
            "applied_files": {key: str(value) for key, value in applied.items()},
        }

    @app.get("/projects/{project_id}/inbox")
    async def get_project_inbox(
        project_id: str,
        unread_only: bool = Query(default=False),
        limit: int = Query(default=100, ge=1, le=500),
    ) -> dict[str, Any]:
        try:
            project_option(project_id, paths.config_dir)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail="Unknown project selection") from exc
        ledger = schedules.ledger(project_id)
        items = ledger.list_inbox(
            project_id=project_id,
            unread_only=unread_only,
            limit=limit,
        )
        return {
            "project_id": project_id,
            "items": items,
            "count": len(items),
            "unread_only": unread_only,
            "delivery": {
                "signed_webhook_configured": schedules.webhook is not None,
                "destination_key": (
                    schedules.webhook.destination_key if schedules.webhook is not None else None
                ),
            },
        }

    @app.post("/projects/{project_id}/inbox/{inbox_id}/read")
    async def mark_project_inbox_read(project_id: str, inbox_id: str) -> dict[str, Any]:
        try:
            project_option(project_id, paths.config_dir)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail="Unknown project selection") from exc
        ledger = schedules.ledger(project_id)
        if not ledger.mark_inbox_read(project_id=project_id, inbox_id=inbox_id):
            raise HTTPException(status_code=404, detail="Inbox item not found")
        item = ledger.inbox_item(project_id=project_id, inbox_id=inbox_id)
        if item is None:
            raise HTTPException(status_code=404, detail="Inbox item not found")
        return item

    @app.post(
        "/projects/{project_id}/schedules",
        status_code=status.HTTP_201_CREATED,
    )
    async def create_project_schedule(
        project_id: str, request: ScheduleRequest
    ) -> dict[str, Any]:
        try:
            project_option(project_id, paths.config_dir)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail="Unknown project selection") from exc
        policy = load_signal_policy(paths.config_dir / "signal_policy.yaml")
        guard = SignalPermissionGuard(policy)
        guard.require("manage_schedules", confirmed=True)

        provider_id = request.provider_id
        if request.mode is RunMode.AGENT:
            provider_id = provider_id or default_provider_id(paths.config_dir)
            if provider_id is None:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail={
                        "code": "schedule_provider_not_ready",
                        "reason": "no_configured_provider",
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
                        "code": "schedule_provider_not_ready",
                        "provider_id": provider_id,
                        "reason": option.reason,
                    },
                )
        try:
            return schedules.create_schedule(
                project_id=project_id,
                cadence=request.cadence,
                timezone_name=request.timezone,
                local_time=request.local_time,
                mode=request.mode,
                provider_id=provider_id if request.mode is RunMode.AGENT else None,
                max_events=request.max_events,
                max_events_per_source=request.max_events_per_source,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.get("/projects/{project_id}/schedules")
    async def list_project_schedules(project_id: str) -> dict[str, Any]:
        try:
            items = schedules.list_schedules(project_id=project_id)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail="Unknown project selection") from exc
        return {"project_id": project_id, "schedules": items, "count": len(items)}

    @app.delete("/projects/{project_id}/schedules/{schedule_id}")
    async def disable_project_schedule(project_id: str, schedule_id: str) -> dict[str, Any]:
        try:
            project_option(project_id, paths.config_dir)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail="Unknown project selection") from exc
        policy = load_signal_policy(paths.config_dir / "signal_policy.yaml")
        SignalPermissionGuard(policy).require("manage_schedules", confirmed=True)
        if not schedules.disable_schedule(project_id=project_id, schedule_id=schedule_id):
            raise HTTPException(status_code=404, detail="Active schedule not found")
        return {"schedule_id": schedule_id, "project_id": project_id, "enabled": False}

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
        payload["profile_revision_id"] = workflow.ledger.scan_profile_revision_id(scan_id=run_id)
        payload["changes_url"] = f"/runs/{run_id}/changes"
        payload["coverage_url"] = f"/runs/{run_id}/coverage"
        payload["product_url"] = f"/runs/{run_id}/product"
        payload["report_url"] = f"/runs/{run_id}/report"
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

    @app.get("/runs/{run_id}/product")
    async def get_run_product(
        run_id: str,
        top: int = Query(default=12, ge=1, le=50),
        all_limit: int = Query(default=20, ge=1, le=1000),
    ) -> dict[str, Any]:
        try:
            product = product_service_for_run(run_id)
            return product.projection(
                run_id, top_count=top, all_limit=all_limit
            ).model_dump(mode="json")
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.get("/runs/{run_id}/report")
    async def get_run_report(
        run_id: str,
        top: int = Query(default=12, ge=1, le=50),
    ) -> dict[str, Any]:
        try:
            product = product_service_for_run(run_id)
            return product.report(run_id, top_count=top).model_dump(mode="json")
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.get("/runs/{run_id}/changes")
    async def get_run_changes(
        run_id: str,
        offset: int = Query(default=0, ge=0),
        limit: int = Query(default=100, ge=1, le=1000),
        query: str = Query(default="", max_length=500),
        decision: str | None = Query(default=None),
        source_type: str | None = Query(default=None),
        category: str | None = Query(default=None),
        impact_group: ImpactGroup | None = Query(default=None),
        analysis: Literal["all", "analyzed", "unanalyzed"] = Query(default="all"),
        sort: Literal["rank", "score", "newest"] = Query(default="rank"),
    ) -> dict[str, Any]:
        try:
            product = product_service_for_run(run_id)
            analyzed = None if analysis == "all" else analysis == "analyzed"
            return product.list_changes(
                run_id,
                offset=offset,
                limit=limit,
                query=query,
                decision=decision,
                source_type=source_type,
                category=category,
                impact_group=impact_group,
                analyzed=analyzed,
                sort=sort,
            ).model_dump(mode="json")
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.get("/runs/{run_id}/changes/{change_id}")
    async def get_run_change_detail(run_id: str, change_id: str) -> dict[str, Any]:
        try:
            product = product_service_for_run(run_id)
            return product.change_detail(change_id, scan_id=run_id).model_dump(mode="json")
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

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

    @app.post(
        "/projects/{project_id}/outcomes",
        status_code=status.HTTP_201_CREATED,
    )
    async def record_project_outcome(
        project_id: str, request: OutcomeRequest
    ) -> dict[str, Any]:
        try:
            project_option(project_id, paths.config_dir)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail="Unknown project selection") from exc
        policy = load_signal_policy(paths.config_dir / "signal_policy.yaml")
        SignalPermissionGuard(policy).require("save_outcome")
        ledger = ChangeLedger(paths.project_state(project_id) / "change_ledger.sqlite3")
        metadata = ledger.scan_metadata(request.scan_id)
        if metadata is None or metadata.get("project_id") != project_id:
            raise HTTPException(status_code=404, detail="Scan not found for project")
        frozen = ledger.scan_change(scan_id=request.scan_id, change_id=request.change_id)
        if frozen is None:
            raise HTTPException(status_code=404, detail="Change not found in frozen Scan")
        return ledger.record_outcome(
            project_id=project_id,
            scan_id=request.scan_id,
            change_id=request.change_id,
            event_revision_id=int(frozen["event_revision_id"]),
            impact_observed=request.impact_observed,
            action_taken=request.action_taken,
            action_helpful=request.action_helpful,
            resolved=request.resolved,
            note=request.note,
            source="api",
        )

    @app.get("/projects/{project_id}/outcomes")
    async def list_project_outcomes(project_id: str) -> dict[str, Any]:
        try:
            project_option(project_id, paths.config_dir)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail="Unknown project selection") from exc
        items = ChangeLedger(
            paths.project_state(project_id) / "change_ledger.sqlite3"
        ).list_outcomes(project_id=project_id)
        return {"project_id": project_id, "items": items, "count": len(items)}

    @app.get("/projects/{project_id}/calibration")
    async def get_project_calibration(
        project_id: str, include_episodes: bool = Query(default=False)
    ) -> dict[str, Any]:
        try:
            snapshot, ledger = project_profile_snapshot(project_id)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail="Unknown project selection") from exc
        policy = load_signal_policy(paths.config_dir / "signal_policy.yaml")
        SignalPermissionGuard(policy).require("read_calibration")
        dataset = build_calibration_dataset(ledger=ledger, project_id=project_id)
        proposal_path = paths.project_state(project_id) / "policy_update_proposal.json"
        proposal = _read_json(proposal_path, {})
        replay_payload: dict[str, Any] | None = None
        if isinstance(proposal, dict) and isinstance(proposal.get("new_policy"), dict):
            replay_payload = evaluate_calibration_replay(
                dataset,
                project_profile=dict(snapshot["effective_profile"]),
                old_policy=policy,
                proposed_policy=dict(proposal["new_policy"]),
            ).model_dump(mode="json")
        return {
            "project_id": project_id,
            "dataset_version": dataset.version,
            "feedback_count": dataset.feedback_count,
            "outcome_count": dataset.outcome_count,
            "episode_count": len(dataset.episodes),
            "labeled_count": dataset.labeled_count,
            "positive_count": dataset.positive_count,
            "negative_count": dataset.negative_count,
            "ambiguous_count": dataset.ambiguous_count,
            "unlabeled_count": dataset.unlabeled_count,
            "orphan_feedback_count": dataset.orphan_feedback_count,
            "minimum_labeled_required": 3,
            "ready_for_replay": dataset.labeled_count >= 3,
            "candidate_replay": replay_payload,
            "episodes": (
                [item.model_dump(mode="json") for item in dataset.episodes]
                if include_episodes
                else []
            ),
        }

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
        ledger = ChangeLedger(project_state / "change_ledger.sqlite3")
        attached = ledger.scan_change_for_event(
            scan_id=request.run_id, event_id=request.signal_id
        )
        ledger.record_calibration_feedback(
            project_id=project_id,
            scan_id=request.run_id,
            change_id=(str(attached["change_id"]) if attached is not None else None),
            event_revision_id=(
                int(attached["event_revision_id"]) if attached is not None else None
            ),
            event_id=request.signal_id,
            label=record.feedback.value,
            note=record.note,
            source="api",
            created_at=record.created_at,
        )
        frozen_change = (
            ledger.scan_change(
                scan_id=request.run_id, change_id=str(attached["change_id"])
            )
            if attached is not None
            else None
        )
        golden_candidate = record_feedback_candidate(
            project_state=project_state,
            project_id=project_id,
            run_id=request.run_id,
            event_id=request.signal_id,
            label=record.feedback,
            note=record.note,
            source="api",
            frozen_change=frozen_change,
        )
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
            "golden_candidate_id": (
                golden_candidate.candidate_id if golden_candidate is not None else None
            ),
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
