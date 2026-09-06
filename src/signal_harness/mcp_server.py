"""Read-only MCP surface over SignalHarness project and run artifacts."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from mcp.server import MCPServer
from mcp.types import ToolAnnotations

from signal_harness.memory import FeedbackMemory, ProjectMemory, SignalMemory
from signal_harness.projects.catalog import default_project_id, project_option
from signal_harness.projects.state import project_state_dir
from signal_harness.resources import resolve_config_dir
from signal_harness.runtime.permissions import SignalPermissionGuard
from signal_harness.signal.policy import load_signal_policy

MCP_TOOL_NAMES = (
    "signalharness_get_project_context",
    "signalharness_search_signal_history",
    "signalharness_get_latest_assessments",
    "signalharness_get_run_trace",
    "signalharness_get_feedback_memory",
)

_RUN_ID = re.compile(r"^run-[A-Za-z0-9_-]{1,64}$")
_READ_ONLY = ToolAnnotations(
    read_only_hint=True,
    destructive_hint=False,
    idempotent_hint=True,
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


def build_mcp_server(
    *,
    cwd: str | Path,
    config_dir: str | Path = "configs",
    output_dir: str | Path = "outputs",
    state_dir: str | Path = ".signal-harness",
) -> MCPServer[None]:
    """Build the MCP server without starting a transport."""

    paths = MCPPaths.resolve(
        cwd=cwd,
        config_dir=config_dir,
        output_dir=output_dir,
        state_dir=state_dir,
    )
    mcp: MCPServer[None] = MCPServer(
        "SignalHarness",
        description=(
            "Read-only project context, signal history, assessments, trace, and "
            "feedback for project-scoped SignalHarness runs."
        ),
    )

    @mcp.tool(
        name="signalharness_get_project_context",
        description="Return stable project profile, watchlist, and active policy metadata.",
        annotations=_READ_ONLY,
        structured_output=True,
    )
    def get_project_context(project_id: str | None = None) -> dict[str, Any]:
        paths.guard("read_project_context")
        selected_id = project_id or default_project_id(paths.config_dir)
        option = project_option(selected_id, paths.config_dir)
        project = ProjectMemory(
            option.project_profile_path,
            option.watchlist_path,
        ).load()
        policy = load_signal_policy(paths.config_dir / "signal_policy.yaml")
        return {
            "project_id": option.id,
            "project_name": option.name,
            **project,
            "policy_version": policy.get("version"),
            "enabled_tools": list(policy.get("enabled_tools", [])),
        }

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
