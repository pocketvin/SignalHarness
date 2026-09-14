"""Thin MCP adapter over SignalHarness project, persistent scans, and product intelligence."""

from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, cast

from mcp.server import MCPServer
from mcp.types import ToolAnnotations

from signal_harness.agent_integration.mode import RunMode
from signal_harness.environment_application import EnvironmentApplication
from signal_harness.memory import FeedbackMemory, ProjectMemory, SignalMemory
from signal_harness.persistence import ChangeLedger
from signal_harness.product_intelligence import ProductIntelligenceService
from signal_harness.providers.catalog import default_provider_id, provider_option
from signal_harness.projects.catalog import default_project_id, project_option
from signal_harness.resources import (
    is_allowed_fixture_path,
    resolve_config_dir,
    resolve_example_path,
)
from signal_harness.runtime.windows import WindowMode
from signal_harness.service_streaming import StreamRunManager
from signal_harness.projects.state import project_state_dir
from signal_harness.runtime.permissions import SignalPermissionGuard
from signal_harness.signal.policy import load_signal_policy

MCP_WRITE_TOOL_NAMES = (
    "signalharness_start_scan",
    "signalharness_start_environment_scan",
    "signalharness_record_change_feedback",
    "signalharness_record_change_outcome",
)

MCP_TOOL_NAMES = (
    "signalharness_get_project_context",
    "signalharness_get_environment_report",
    "signalharness_list_environment_changes",
    "signalharness_get_environment_change",
    "signalharness_get_project_architecture",
    "signalharness_get_project_activity",
    "signalharness_get_calibration_status",
    "signalharness_start_environment_scan",
    "signalharness_record_change_feedback",
    "signalharness_record_change_outcome",
    # Compatibility surfaces retained for legacy Scan/Harness consumers.
    "signalharness_search_signal_history",
    "signalharness_get_latest_assessments",
    "signalharness_get_run_trace",
    "signalharness_get_feedback_memory",
    "signalharness_start_scan",
    "signalharness_get_scan_status",
    "signalharness_get_product",
    "signalharness_list_changes",
    "signalharness_get_change_detail",
)

_RUN_ID = re.compile(r"^run-[A-Za-z0-9_-]{1,64}$")
_READ_ONLY = ToolAnnotations(
    read_only_hint=True,
    destructive_hint=False,
    idempotent_hint=True,
    open_world_hint=False,
)
_START_SCAN = ToolAnnotations(
    read_only_hint=False,
    destructive_hint=False,
    idempotent_hint=False,
    open_world_hint=True,
)
_PRODUCT_WRITE = ToolAnnotations(
    read_only_hint=False,
    destructive_hint=False,
    idempotent_hint=False,
    open_world_hint=False,
)


@dataclass(frozen=True)
class MCPPaths:
    """Resolved locations exposed by the local MCP server."""

    cwd: Path
    config_dir: Path
    output_dir: Path
    state_dir: Path

    @classmethod
    def resolve(
        cls,
        *,
        cwd: str | Path,
        config_dir: str | Path = "configs",
        output_dir: str | Path = "outputs",
        state_dir: str | Path = ".signal-harness",
    ) -> "MCPPaths":
        root = Path(cwd).expanduser().resolve()
        return cls(
            cwd=root,
            config_dir=resolve_config_dir(root, config_dir),
            output_dir=_resolve(root, output_dir),
            state_dir=_resolve(root, state_dir),
        )

    def guard(self, action: str) -> None:
        policy = load_signal_policy(self.config_dir / "signal_policy.yaml")
        SignalPermissionGuard(policy).require(action)

    def output_for_run(self, run_id: str | None) -> Path:
        return _run_dir(self.output_dir, run_id)

    def state_for_run(self, run_id: str | None) -> Path:
        return _run_dir(self.state_dir, run_id)

    def state_for_project(self, project_id: str) -> Path:
        project_option(project_id, self.config_dir)
        return project_state_dir(self.state_dir, project_id)

    def project_id_for_run(self, run_id: str) -> str | None:
        payload = _read_json(self.output_for_run(run_id) / "service_run.json", {})
        if not isinstance(payload, dict):
            return None
        value = str(payload.get("project_id") or "").strip()
        return value or None

    def project_state_scope(
        self,
        *,
        project_id: str | None = None,
        run_id: str | None = None,
    ) -> Path:
        if project_id is not None:
            return self.state_for_project(project_id)
        if run_id is not None:
            selected = self.project_id_for_run(run_id)
            if selected is not None:
                return self.state_for_project(selected)
            return self.state_for_run(run_id)
        return self.state_for_project(default_project_id(self.config_dir))


class MCPRuntime:
    """Delegate MCP product operations to the existing durable runtime/services."""

    def __init__(
        self,
        *,
        paths: MCPPaths,
        stream_manager: StreamRunManager | None = None,
        environment_application: EnvironmentApplication | None = None,
    ) -> None:
        self.paths = paths
        self.streams = stream_manager or StreamRunManager(
            cwd=paths.cwd,
            config_dir=paths.config_dir,
            output_dir=paths.output_dir,
            state_dir=paths.state_dir,
        )
        self.environment = environment_application or EnvironmentApplication(
            cwd=paths.cwd,
            config_dir=paths.config_dir,
            output_dir=paths.output_dir,
            state_dir=paths.state_dir,
            streams=self.streams,
        )
        self._recovered = stream_manager is not None
        self._recovery_lock: asyncio.Lock | None = None

    async def ensure_recovered(self) -> None:
        """Recover persisted unfinished runs once for standalone stdio MCP."""

        if self._recovered:
            return
        if self._recovery_lock is None:
            self._recovery_lock = asyncio.Lock()
        async with self._recovery_lock:
            if not self._recovered:
                await self.streams.recover_pending()
                self._recovered = True

    def _product_service(self, run_id: str) -> ProductIntelligenceService:
        validated = validate_run_id(run_id)
        payload = _read_json(self.paths.output_for_run(validated) / "service_run.json", {})
        if not isinstance(payload, dict):
            raise ValueError(f"Run metadata is unavailable: {validated}")
        project_id = str(payload.get("project_id") or "").strip()
        if not project_id:
            raise ValueError(f"Run project is unavailable: {validated}")
        return ProductIntelligenceService(
            ledger=ChangeLedger(self.paths.state_for_project(project_id) / "change_ledger.sqlite3"),
            project_id=project_id,
        )

    async def start_scan(
        self,
        *,
        project_id: str | None,
        source_mode: Literal["live", "fixture"],
        fixture: str | None,
        mode: Literal["demo", "mock-agent", "agent"],
        provider_id: str | None,
        window: Literal["since_last", "24h", "7d", "30d"],
        max_events: int | None,
        max_events_per_source: int | None,
    ) -> dict[str, Any]:
        await self.ensure_recovered()
        if max_events is not None and max_events < 1:
            raise ValueError("max_events must be positive")
        if max_events_per_source is not None and max_events_per_source < 1:
            raise ValueError("max_events_per_source must be positive")
        selected_project = project_option(
            project_id or default_project_id(self.paths.config_dir),
            self.paths.config_dir,
        )
        selected_mode = RunMode(mode)
        selected_provider = provider_id
        if selected_mode is RunMode.AGENT:
            selected_provider = selected_provider or default_provider_id(self.paths.config_dir)
            if selected_provider is None:
                raise ValueError("agent mode requires a configured provider")
            option = provider_option(selected_provider, self.paths.config_dir)
            if not option.ready:
                raise ValueError(
                    f"Provider {selected_provider} is not ready: {option.reason or 'unknown'}"
                )

        resolved_fixture: Path | None = None
        window_mode: WindowMode
        if source_mode == "fixture":
            resolved_fixture = self._safe_fixture(
                fixture or "examples/signal_harness/sample_events.json"
            )
            window_mode = "legacy"
        else:
            window_mode = cast(WindowMode, window)

        session = self.streams.start(
            source_mode=source_mode,
            fixture=resolved_fixture,
            since=None,
            until=None,
            window_mode=window_mode,
            mode=selected_mode,
            provider_id=selected_provider if selected_mode is RunMode.AGENT else None,
            project=selected_project,
            max_events=max_events,
            max_events_per_source=max_events_per_source,
        )
        self.streams.ensure_started(session)
        return {
            **session.public_payload(),
            "product_url": f"/runs/{session.run_id}/product",
            "report_url": f"/runs/{session.run_id}/report",
            "changes_url": f"/runs/{session.run_id}/changes",
        }

    async def status(self, run_id: str) -> dict[str, Any]:
        await self.ensure_recovered()
        validated = validate_run_id(run_id)
        session = self.streams.get(validated)
        if session is not None:
            payload = session.result or session.public_payload()
            return {
                **payload,
                "product_ready": session.status == "success",
                "product_url": f"/runs/{validated}/product",
                "report_url": f"/runs/{validated}/report",
                "changes_url": f"/runs/{validated}/changes",
            }
        payload = _read_json(self.paths.output_for_run(validated) / "service_run.json", None)
        if not isinstance(payload, dict):
            raise ValueError(f"Run metadata is unavailable: {validated}")
        status = str(payload.get("status") or "unknown")
        return {
            **payload,
            "product_ready": status == "success",
            "product_url": f"/runs/{validated}/product",
            "report_url": f"/runs/{validated}/report",
            "changes_url": f"/runs/{validated}/changes",
        }

    def product(self, run_id: str, *, top: int, all_limit: int) -> dict[str, Any]:
        self._require_success(run_id)
        return self._product_service(run_id).projection(
            run_id, top_count=top, all_limit=all_limit
        ).model_dump(mode="json")

    def list_changes(
        self,
        run_id: str,
        *,
        offset: int,
        limit: int,
        query: str,
        decision: str | None,
        source_type: str | None,
        category: str | None,
        analysis: Literal["all", "analyzed", "unanalyzed"],
        sort: Literal["rank", "score", "newest"],
    ) -> dict[str, Any]:
        self._require_success(run_id)
        analyzed = None if analysis == "all" else analysis == "analyzed"
        return self._product_service(run_id).list_changes(
            run_id,
            offset=offset,
            limit=limit,
            query=query,
            decision=decision,
            source_type=source_type,
            category=category,
            analyzed=analyzed,
            sort=sort,
        ).model_dump(mode="json")

    def change_detail(self, run_id: str, change_id: str) -> dict[str, Any]:
        self._require_success(run_id)
        return self._product_service(run_id).change_detail(
            change_id, scan_id=run_id
        ).model_dump(mode="json")

    def _safe_fixture(self, value: str) -> Path:
        resolved = resolve_example_path(self.paths.cwd, value)
        if not is_allowed_fixture_path(resolved, self.paths.cwd):
            raise ValueError("Fixture must stay inside the project or bundled examples")
        if resolved.suffix.lower() != ".json" or not resolved.is_file():
            raise ValueError("Fixture must be an existing JSON file")
        return resolved

    def _require_success(self, run_id: str) -> None:
        validated = validate_run_id(run_id)
        payload = _read_json(self.paths.output_for_run(validated) / "service_run.json", {})
        if not isinstance(payload, dict) or payload.get("status") != "success":
            status = payload.get("status") if isinstance(payload, dict) else "unknown"
            raise ValueError(
                f"Scan {validated} is not complete (status={status}); call "
                "signalharness_get_scan_status first"
            )


def build_mcp_server(
    *,
    cwd: str | Path,
    config_dir: str | Path = "configs",
    output_dir: str | Path = "outputs",
    state_dir: str | Path = ".signal-harness",
    stream_manager: StreamRunManager | None = None,
    environment_application: EnvironmentApplication | None = None,
) -> MCPServer[None]:
    """Build the MCP server without starting a transport."""

    paths = MCPPaths.resolve(
        cwd=cwd,
        config_dir=config_dir,
        output_dir=output_dir,
        state_dir=state_dir,
    )
    runtime = MCPRuntime(
        paths=paths,
        stream_manager=stream_manager,
        environment_application=environment_application,
    )
    mcp: MCPServer[None] = MCPServer(
        "SignalHarness",
        description=(
            "Project Environment Intelligence adapter over the shared application layer. "
            "Current tools expose Profile/Architecture/Report/Change/learning semantics; "
            "legacy Scan/Harness tools remain compatibility-only."
        ),
    )

    @mcp.tool(
        name="signalharness_get_project_context",
        description=(
            "Return the effective Project Profile, Architecture Snapshot, watchlist and policy "
            "metadata used by the current environment product."
        ),
        annotations=_READ_ONLY,
        structured_output=True,
    )
    def get_project_context(project_id: str | None = None) -> dict[str, Any]:
        paths.guard("read_project_context")
        selected_id = project_id or default_project_id(paths.config_dir)
        option = project_option(selected_id, paths.config_dir)
        snapshot = runtime.environment.profile_snapshot(selected_id)
        project = ProjectMemory(
            option.project_profile_path,
            option.watchlist_path,
        ).load()
        policy = load_signal_policy(paths.config_dir / "signal_policy.yaml")
        return {
            "project_id": option.id,
            "project_name": option.name,
            "profile_revision_id": snapshot.get("profile_revision_id"),
            "project_profile": snapshot.get("effective_profile", {}),
            "auto_profile": snapshot.get("auto_profile", {}),
            "preferences": snapshot.get("preferences", []),
            "watchlist": project["watchlist"],
            "policy_version": policy.get("version"),
            "enabled_tools": list(policy.get("enabled_tools", [])),
        }

    @mcp.tool(
        name="signalharness_get_environment_report",
        description=(
            "Return one saved Direction-first EnvironmentReport including Brief, Directions, "
            "Radar and Featured Changes. Uses the latest report when scan_id is omitted."
        ),
        annotations=_READ_ONLY,
        structured_output=True,
    )
    def get_environment_report(
        project_id: str | None = None,
        scan_id: str | None = None,
    ) -> dict[str, Any]:
        selected = project_id or default_project_id(paths.config_dir)
        return runtime.environment.require_report(selected, scan_id)

    @mcp.tool(
        name="signalharness_list_environment_changes",
        description=(
            "List/search the same frozen Change projection used by the Direction-first Web UI. "
            "Direction evidence can be selected with direction_id."
        ),
        annotations=_READ_ONLY,
        structured_output=True,
    )
    def list_environment_changes(
        project_id: str | None = None,
        scan_id: str | None = None,
        view: Literal["all", "relevant", "featured", "activity", "unavailable"] = "relevant",
        query: str = "",
        direction_id: str | None = None,
        offset: int = 0,
        limit: int = 25,
    ) -> dict[str, Any]:
        if offset < 0:
            raise ValueError("offset must be non-negative")
        selected = project_id or default_project_id(paths.config_dir)
        report = runtime.environment.require_report(selected, scan_id)
        return runtime.environment.changes(
            selected,
            str(report["scan_id"]),
            view=view,
            query=query[:400],
            direction_id=direction_id,
            offset=offset,
            limit=_bounded_product_limit(limit, maximum=100),
        )

    @mcp.tool(
        name="signalharness_get_environment_change",
        description="Return one frozen Direction-first environment Change with its saved evidence.",
        annotations=_READ_ONLY,
        structured_output=True,
    )
    def get_environment_change(
        change_id: str,
        project_id: str | None = None,
        scan_id: str | None = None,
    ) -> dict[str, Any]:
        selected = project_id or default_project_id(paths.config_dir)
        report = runtime.environment.require_report(selected, scan_id)
        return runtime.environment.change(selected, str(report["scan_id"]), change_id)

    @mcp.tool(
        name="signalharness_get_project_architecture",
        description=(
            "Return the current evidence-backed Architecture Snapshot: subsystems, entrypoint "
            "hints, dependency imports and bounded static-import edges."
        ),
        annotations=_READ_ONLY,
        structured_output=True,
    )
    def get_project_architecture(project_id: str | None = None) -> dict[str, Any]:
        selected = project_id or default_project_id(paths.config_dir)
        return runtime.environment.architecture(selected)

    @mcp.tool(
        name="signalharness_get_project_activity",
        description=(
            "Return the deterministic project-activity work-area summary for a saved environment "
            "report. Project activity never proves an external Direction."
        ),
        annotations=_READ_ONLY,
        structured_output=True,
    )
    def get_project_activity(
        project_id: str | None = None,
        scan_id: str | None = None,
    ) -> dict[str, Any]:
        selected = project_id or default_project_id(paths.config_dir)
        report = runtime.environment.require_report(selected, scan_id)
        return runtime.environment.activity_summary(selected, str(report["scan_id"]))

    @mcp.tool(
        name="signalharness_get_calibration_status",
        description=(
            "Return review-first learning readiness from durable Change feedback/outcomes and "
            "candidate replay; this never auto-applies policy."
        ),
        annotations=_READ_ONLY,
        structured_output=True,
    )
    def get_calibration_status(
        project_id: str | None = None,
        include_episodes: bool = False,
    ) -> dict[str, Any]:
        selected = project_id or default_project_id(paths.config_dir)
        return runtime.environment.calibration_status(
            selected, include_episodes=include_episodes
        )

    @mcp.tool(
        name="signalharness_start_environment_scan",
        description=(
            "Start the current live Direction-first environment scan with backend-owned model "
            "routing. No fixture/mode/provider knobs are exposed."
        ),
        annotations=_START_SCAN,
        structured_output=True,
    )
    async def start_environment_scan(
        project_id: str | None = None,
        window: Literal["since_last", "24h", "7d", "30d"] = "since_last",
    ) -> dict[str, Any]:
        await runtime.ensure_recovered()
        selected = project_id or default_project_id(paths.config_dir)
        return runtime.environment.start_scan(selected, window=window)

    @mcp.tool(
        name="signalharness_record_change_feedback",
        description=(
            "Attach review-first learning feedback to one frozen environment Change. Creates a "
            "review proposal but never applies policy automatically."
        ),
        annotations=_PRODUCT_WRITE,
        structured_output=True,
    )
    def record_change_feedback(
        change_id: str,
        label: Literal["useful", "not_useful", "false_positive", "too_generic"],
        project_id: str | None = None,
        scan_id: str | None = None,
        note: str = "",
    ) -> dict[str, Any]:
        selected = project_id or default_project_id(paths.config_dir)
        report = runtime.environment.require_report(selected, scan_id)
        return runtime.environment.record_feedback(
            selected,
            str(report["scan_id"]),
            change_id,
            label=label,
            note=note[:2000],
            source="environment-mcp",
        )

    @mcp.tool(
        name="signalharness_record_change_outcome",
        description=(
            "Record observed Change outcomes for calibration. Historical reports remain frozen "
            "and are never rewritten by this tool."
        ),
        annotations=_PRODUCT_WRITE,
        structured_output=True,
    )
    def record_change_outcome(
        change_id: str,
        project_id: str | None = None,
        scan_id: str | None = None,
        impact_observed: bool | None = None,
        action_taken: bool | None = None,
        action_helpful: bool | None = None,
        resolved: bool | None = None,
        note: str = "",
    ) -> dict[str, Any]:
        selected = project_id or default_project_id(paths.config_dir)
        report = runtime.environment.require_report(selected, scan_id)
        return runtime.environment.record_outcome(
            selected,
            str(report["scan_id"]),
            change_id,
            impact_observed=impact_observed,
            action_taken=action_taken,
            action_helpful=action_helpful,
            resolved=resolved,
            note=note[:2000],
            source="environment-mcp",
        )

    @mcp.tool(
        name="signalharness_search_signal_history",
        description=(
            "Search prior SignalHarness assessments by free-text query; optionally "
            "target one service run."
        ),
        annotations=_READ_ONLY,
        structured_output=True,
    )
    def search_signal_history(
        query: str = "",
        project_id: str | None = None,
        run_id: str | None = None,
        limit: int = 20,
    ) -> dict[str, Any]:
        paths.guard("read_signal_history")
        bounded_limit = _bounded_limit(limit)
        memory = SignalMemory(
            paths.project_state_scope(project_id=project_id, run_id=run_id) / "signal_memory.json"
        ).load()
        items = [item for item in memory.get("previous_assessments", []) if isinstance(item, dict)]
        filtered = _filter_items(items, query)
        return _collection_payload(filtered, bounded_limit)

    @mcp.tool(
        name="signalharness_get_latest_assessments",
        description="Return latest structured assessments, optionally filtered by decision.",
        annotations=_READ_ONLY,
        structured_output=True,
    )
    def get_latest_assessments(
        project_id: str | None = None,
        run_id: str | None = None,
        decision: str | None = None,
        limit: int = 50,
    ) -> dict[str, Any]:
        paths.guard("read_assessments")
        if run_id is not None:
            payload = _read_json(paths.output_for_run(run_id) / "impact_scores.json", [])
            items = (
                [item for item in payload if isinstance(item, dict)]
                if isinstance(payload, list)
                else []
            )
        else:
            memory = SignalMemory(
                paths.project_state_scope(project_id=project_id) / "signal_memory.json"
            ).load()
            previous = memory.get("previous_assessments", [])
            items = (
                [item for item in previous if isinstance(item, dict)]
                if isinstance(previous, list)
                else []
            )
        if decision:
            normalized = decision.strip().lower()
            items = [item for item in items if str(item.get("decision", "")).lower() == normalized]
        return _collection_payload(items, _bounded_limit(limit))

    @mcp.tool(
        name="signalharness_get_run_trace",
        description="Return latest workflow trace, optionally filtered by Agent or step name.",
        annotations=_READ_ONLY,
        structured_output=True,
    )
    def get_run_trace(
        run_id: str | None = None,
        agent: str | None = None,
        limit: int = 50,
    ) -> dict[str, Any]:
        paths.guard("read_run_trace")
        payload = _read_json(paths.output_for_run(run_id) / "task_trace.json", [])
        items = (
            [item for item in payload if isinstance(item, dict)]
            if isinstance(payload, list)
            else []
        )
        if agent:
            needle = agent.strip().lower()
            items = [
                item
                for item in items
                if needle
                in " ".join(
                    str(item.get(key) or "").lower() for key in ("agent_name", "agent", "step")
                )
            ]
        return _collection_payload(items, _bounded_limit(limit))

    @mcp.tool(
        name="signalharness_get_feedback_memory",
        description="Return human feedback records, optionally from one service run.",
        annotations=_READ_ONLY,
        structured_output=True,
    )
    def get_feedback_memory(
        project_id: str | None = None,
        run_id: str | None = None,
        limit: int = 50,
    ) -> dict[str, Any]:
        paths.guard("read_feedback_memory")
        records = FeedbackMemory(
            paths.project_state_scope(project_id=project_id, run_id=run_id) / "feedback_memory.json"
        ).load()
        items = [record.model_dump(mode="json") for record in records]
        return _collection_payload(items, _bounded_limit(limit))


    @mcp.tool(
        name="signalharness_start_scan",
        description=(
            "Start a fresh persistent SignalHarness scan and return immediately with a run handle. "
            "Use fixture + demo/mock-agent for offline work; live mode may access configured sources."
        ),
        annotations=_START_SCAN,
        structured_output=True,
    )
    async def start_scan(
        project_id: str | None = None,
        source_mode: Literal["live", "fixture"] = "live",
        fixture: str | None = None,
        mode: Literal["demo", "mock-agent", "agent"] = "mock-agent",
        provider_id: str | None = None,
        window: Literal["since_last", "24h", "7d", "30d"] = "since_last",
        max_events: int | None = 12,
        max_events_per_source: int | None = 4,
    ) -> dict[str, Any]:
        return await runtime.start_scan(
            project_id=project_id,
            source_mode=source_mode,
            fixture=fixture,
            mode=mode,
            provider_id=provider_id,
            window=window,
            max_events=max_events,
            max_events_per_source=max_events_per_source,
        )

    @mcp.tool(
        name="signalharness_get_scan_status",
        description=(
            "Read persistent scan status by run_id. When product_ready is false, poll this tool "
            "before requesting product results."
        ),
        annotations=_READ_ONLY,
        structured_output=True,
    )
    async def get_scan_status(run_id: str) -> dict[str, Any]:
        return await runtime.status(run_id)

    @mcp.tool(
        name="signalharness_get_product",
        description="Return Overall Report, Top Changes, and the first All Relevant Changes page.",
        annotations=_READ_ONLY,
        structured_output=True,
    )
    def get_product(run_id: str, top: int = 12, all_limit: int = 20) -> dict[str, Any]:
        return runtime.product(
            run_id,
            top=_bounded_product_limit(top, maximum=50),
            all_limit=_bounded_product_limit(all_limit, maximum=1000),
        )

    @mcp.tool(
        name="signalharness_list_changes",
        description=(
            "List/search/filter a frozen scan's All Relevant Changes with stable pagination."
        ),
        annotations=_READ_ONLY,
        structured_output=True,
    )
    def list_changes(
        run_id: str,
        offset: int = 0,
        limit: int = 20,
        query: str = "",
        decision: str | None = None,
        source_type: str | None = None,
        category: str | None = None,
        analysis: Literal["all", "analyzed", "unanalyzed"] = "all",
        sort: Literal["rank", "score", "newest"] = "rank",
    ) -> dict[str, Any]:
        if offset < 0:
            raise ValueError("offset must be non-negative")
        return runtime.list_changes(
            run_id,
            offset=offset,
            limit=_bounded_product_limit(limit, maximum=1000),
            query=query[:500],
            decision=decision,
            source_type=source_type,
            category=category,
            analysis=analysis,
            sort=sort,
        )

    @mcp.tool(
        name="signalharness_get_change_detail",
        description="Return one Change detail from a completed frozen scan.",
        annotations=_READ_ONLY,
        structured_output=True,
    )
    def get_change_detail(run_id: str, change_id: str) -> dict[str, Any]:
        return runtime.change_detail(run_id, change_id)

    return mcp


def _resolve(root: Path, value: str | Path) -> Path:
    target = Path(value).expanduser()
    return target.resolve() if target.is_absolute() else (root / target).resolve()


def validate_run_id(run_id: str) -> str:
    """Validate the public service-run identifier format."""

    if not _RUN_ID.fullmatch(run_id):
        raise ValueError("run_id must match run-<letters/numbers/_/->")
    return run_id


def _run_dir(base: Path, run_id: str | None) -> Path:
    if run_id is None:
        return base
    validated = validate_run_id(run_id)
    candidate = (base / "service-runs" / validated).resolve()
    expected_parent = (base / "service-runs").resolve()
    if candidate.parent != expected_parent:
        raise ValueError("run_id resolved outside service-runs")
    if not candidate.exists():
        raise ValueError(f"Unknown run_id: {run_id}")
    return candidate


def _read_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return default


def _bounded_limit(limit: int) -> int:
    return max(1, min(100, limit))


def _bounded_product_limit(value: int, *, maximum: int) -> int:
    if value < 1:
        raise ValueError("limit must be positive")
    return min(maximum, value)


def _filter_items(items: list[dict[str, Any]], query: str) -> list[dict[str, Any]]:
    needle = query.strip().lower()
    if not needle:
        return items
    return [
        item
        for item in items
        if needle in json.dumps(item, ensure_ascii=False, sort_keys=True).lower()
    ]


def _collection_payload(items: list[dict[str, Any]], limit: int) -> dict[str, Any]:
    return {
        "items": items[:limit],
        "count": len(items),
        "returned": min(len(items), limit),
        "truncated": len(items) > limit,
    }
