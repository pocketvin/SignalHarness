"""Controlled read-only evidence tool loop for LLM Agents."""

from __future__ import annotations

from typing import Any, Protocol, TypedDict

from signal_harness.agent_integration.schemas import (
    ContextEvidenceOutput,
    EvidenceToolPlan,
    ToolObservation,
    ToolRequest,
)
from signal_harness.runtime.cache import ToolObservationCache, stable_hash
from signal_harness.runtime.permissions import SignalPermissionGuard
from signal_harness.runtime.tool_executor import SignalToolExecutor

READ_ONLY_TOOL_ALLOWLIST = frozenset(
    {
        "github_signal",
        "rss_signal",
        "web_change",
        "signal_memory",
        "signal_score",
    }
)
EXPLICITLY_BLOCKED_TOOLS = frozenset(
    {
        "bash",
        "shell",
        "edit_file",
        "write_file",
        "mcp",
        "create_github_issue",
        "send_team_notification",
        "modify_signal_policy",
    }
)


class ToolExecutionTrace(TypedDict):
    executed: list[str]
    errors: list[str]
    blocked: list[str]
    budget_blocked: list[str]
    permission_checks: list[str]
    cache_events: list[str]


class ToolLoopLimitValues(Protocol):
    @property
    def max_total_tool_requests_per_run(self) -> int: ...

    @property
    def max_tool_requests_per_event(self) -> int: ...

    @property
    def max_tool_output_chars(self) -> int: ...


class ControlledToolLoop:
    """Execute model-requested tools while Python owns permissions and budget."""

    def __init__(
        self,
        *,
        tool_executor: SignalToolExecutor | None,
        limits: ToolLoopLimitValues,
    ) -> None:
        self.tool_executor = tool_executor
        self.limits = limits
        self.tool_cache = ToolObservationCache()
        self.tool_requests_used = 0

    async def execute_tool_requests(
        self,
        plan: EvidenceToolPlan,
        policy: dict[str, Any],
        *,
        event_count: int,
    ) -> tuple[list[ToolObservation], ToolExecutionTrace]:
        observations: list[ToolObservation] = []
        trace: ToolExecutionTrace = {
            "executed": [],
            "errors": [],
            "blocked": [],
            "budget_blocked": [],
            "permission_checks": [],
            "cache_events": [],
        }
        guard = SignalPermissionGuard(policy)
        max_for_events = max(1, event_count) * self.limits.max_tool_requests_per_event
        remaining_for_run = max(
            0,
            self.limits.max_total_tool_requests_per_run - self.tool_requests_used,
        )
        allowed_count = min(len(plan.tool_requests), max_for_events, remaining_for_run)
        for index, request in enumerate(plan.tool_requests):
            if index >= allowed_count:
                trace["blocked"].append(request.tool_name)
                trace["budget_blocked"].append(request.tool_name)
                trace["permission_checks"].append(
                    f"{request.tool_name}:blocked:budget_exceeded"
                )
                observations.append(
                    ToolObservation(
                        tool_name=request.tool_name,
                        status="blocked",
                        output_summary=(
                            "Tool request skipped because the tool budget was exceeded."
                        ),
                        error="budget_exceeded",
                    )
                )
                continue
            self.tool_requests_used += 1
            observation = await self._execute_one_tool(request, guard, trace)
            observations.append(observation)
        return observations, trace

    async def _execute_one_tool(
        self,
        request: ToolRequest,
        guard: SignalPermissionGuard,
        trace: ToolExecutionTrace,
    ) -> ToolObservation:
        name = request.tool_name
        if (
            name in EXPLICITLY_BLOCKED_TOOLS
            or name not in READ_ONLY_TOOL_ALLOWLIST
            or not request_is_read_only(name, request.arguments)
        ):
            trace["blocked"].append(name)
            trace["permission_checks"].append(f"{name}:blocked:not-read-only-allowlisted")
            return ToolObservation(
                tool_name=name,
                status="blocked",
                output_summary="Tool request was blocked by the read-only allowlist.",
                error=f"{name} is not an allowed evidence tool",
            )
        permission = permission_action(name, request.arguments)
        decision = guard.evaluate(permission)
        trace["permission_checks"].append(
            f"{name}:{permission}:{'allowed' if decision.allowed else 'blocked'}"
        )
        if not decision.allowed:
            trace["blocked"].append(name)
            return ToolObservation(
                tool_name=name,
                status="blocked",
                output_summary=decision.reason,
                error=decision.reason,
            )
        if self.tool_executor is None:
            error = "SignalToolExecutor is unavailable"
            trace["errors"].append(f"{name}: {error}")
            return ToolObservation(
                tool_name=name,
                status="error",
                output_summary=error,
                error=error,
            )
        cache_key = self.tool_cache.key(name, request.arguments)
        cached = self.tool_cache.get(cache_key)
        if isinstance(cached, ToolObservation):
            trace["cache_events"].append(f"tool_observation:{name}:hit")
            return cached
        trace["cache_events"].append(f"tool_observation:{name}:miss")
        result = await self.tool_executor.call(name, request.arguments)
        raw_ref = f"sha256:{stable_hash(result.output)}"
        output_limit = self.limits.max_tool_output_chars
        if result.is_error:
            trace["errors"].append(f"{name}: {result.output}")
            observation = ToolObservation(
                tool_name=name,
                status="error",
                output_summary=result.output[:output_limit],
                raw_output_ref=raw_ref,
                error=result.output,
            )
        else:
            trace["executed"].append(name)
            observation = ToolObservation(
                tool_name=name,
                status="success",
                output_summary=result.output[:output_limit],
                raw_output_ref=raw_ref,
            )
        self.tool_cache.put(cache_key, observation)
        return observation


def permission_action(name: str, arguments: dict[str, Any]) -> str:
    if name == "github_signal":
        return (
            "read_github_issue"
            if arguments.get("action") == "fetch_repo_issues"
            else "read_github_release"
        )
    if name == "rss_signal":
        return "read_rss"
    if name == "web_change":
        return "read_mock_web_change"
    return "read_config"


def request_is_read_only(name: str, arguments: dict[str, Any]) -> bool:
    if name == "signal_memory":
        return str(arguments.get("action", "")).startswith("load_")
    return name in READ_ONLY_TOOL_ALLOWLIST


def evidence_exit_condition(
    plan: EvidenceToolPlan,
    observations: list[ToolObservation],
) -> str:
    if not plan.tool_requests:
        return "no_tool_requests"
    if any(item.error == "budget_exceeded" for item in observations):
        return "completed_with_budget_blocks"
    if any(item.status == "error" for item in observations):
        return "completed_with_tool_errors"
    if any(item.status == "blocked" for item in observations):
        return "completed_with_blocked_tools"
    return "evidence_complete"


def cap_evidence_after_tool_failures(
    evidence: ContextEvidenceOutput,
    observations: list[ToolObservation],
) -> ContextEvidenceOutput:
    failures = [
        item for item in observations if item.status in {"error", "blocked"}
    ]
    if not failures:
        return evidence
    message = "; ".join(
        f"{item.tool_name}: {item.error or item.output_summary}"
        for item in failures
    )
    return ContextEvidenceOutput(
        results=[
            item.model_copy(
                update={
                    "confidence": min(item.confidence, 0.55),
                    "uncertainty": " ".join(
                        value for value in (item.uncertainty, message) if value
                    ),
                    "tool_errors": list(dict.fromkeys([*item.tool_errors, message])),
                }
            )
            for item in evidence.results
        ]
    )
