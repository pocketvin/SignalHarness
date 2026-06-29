"""Five-Agent execution with controlled tools and deterministic safety."""

from __future__ import annotations

import json
import re
import time
import asyncio
from collections.abc import Callable, Iterable
from dataclasses import dataclass, replace
from typing import Any, TypeVar, TypedDict

from pydantic import BaseModel

from signal_harness.agent_integration.mode import RunMode
from signal_harness.agent_integration.schemas import (
    ActionOutput,
    ContextEvidenceOutput,
    EvidenceToolPlan,
    ImpactOutput,
    LearningPolicyOutput,
    SupervisorOutput,
    ToolObservation,
    ToolRequest,
)
from signal_harness.agent_integration.trace import append_llm_trace
from signal_harness.agent_team import (
    ActionPlannerAgent,
    ContextEvidenceAgent,
    ImpactAnalystAgent,
    LearningPolicyAgent,
    SignalSupervisorAgent,
)
from signal_harness.providers.adapter import AgentCall, AgentProvider
from signal_harness.runtime.cache import ToolObservationCache, stable_hash
from signal_harness.runtime.permissions import SignalPermissionGuard
from signal_harness.runtime.tool_executor import SignalToolExecutor
from signal_harness.runtime.tracing import TraceRecorder
from signal_harness.signal.policy import decision_for_score
from signal_harness.signal.scorer import score_signal
from signal_harness.signal.schemas import (
    AgentScoreBreakdown,
    FeedbackRecord,
    NoiseAssessment,
    SignalAssessment,
    SignalCategory,
    SignalCluster,
    SignalDecision,
    SignalEvent,
    TraceStep,
)

OutputT = TypeVar("OutputT", bound=BaseModel)

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


@dataclass(frozen=True)
class AgentLoopLimits:
    """Bound LLM calls, schema repair, and controlled evidence tool use."""

    max_schema_retries: int = 1
    max_agent_call_seconds: int = 45
    max_run_seconds: int = 180
    max_total_tool_requests_per_run: int = 20
    max_tool_requests_per_event: int = 3
    max_tool_output_chars: int = 1000
    max_repair_rounds_per_run: int = 1
    max_repair_events_per_run: int = 5


ToolLoopLimits = AgentLoopLimits


class ToolExecutionTrace(TypedDict):
    executed: list[str]
    errors: list[str]
    blocked: list[str]
    budget_blocked: list[str]
    permission_checks: list[str]
    cache_events: list[str]


def _json_object(text: str) -> dict[str, Any]:
    stripped = text.strip()
    if not stripped:
        raise ValueError("Agent response was empty")
    if stripped.startswith("```"):
        stripped = re.sub(r"^```(?:json)?\s*", "", stripped)
        stripped = re.sub(r"\s*```$", "", stripped)
    payload = json.loads(stripped)
    if not isinstance(payload, dict):
        raise ValueError("Agent response must be a JSON object")
    return payload


def _output_count(output: BaseModel) -> int:
    payload = output.model_dump(mode="json")
    for key in ("routes", "results", "tool_requests"):
        value = payload.get(key)
        if isinstance(value, list):
            return len(value)
    if isinstance(output, LearningPolicyOutput):
        return 3
    return 1


def _short_error(exc: Exception, *, limit: int = 320) -> str:
    message = re.sub(r"\s+", " ", str(exc)).strip()
    if not message:
        message = exc.__class__.__name__
    return message[:limit]


def _trace_tools(output: BaseModel) -> tuple[list[str], list[str], list[str], list[str]]:
    payload = output.model_dump(mode="json")
    source_types: list[str] = []
    requested: list[str] = []
    executed: list[str] = []
    errors: list[str] = []
    if isinstance(payload.get("source_types_observed"), list):
        source_types.extend(str(item) for item in payload["source_types_observed"])
    if isinstance(payload.get("tool_requests"), list):
        requested.extend(
            str(item.get("tool_name"))
            for item in payload["tool_requests"]
            if isinstance(item, dict) and item.get("tool_name")
        )
    for result in payload.get("results", []):
        if not isinstance(result, dict):
            continue
        source_types.extend(str(item) for item in result.get("source_types_observed", []))
        requested.extend(str(item) for item in result.get("tools_requested", []))
        executed.extend(str(item) for item in result.get("tools_executed", []))
        errors.extend(str(item) for item in result.get("tool_errors", []))
    if isinstance(payload.get("tools_requested"), list):
        requested.extend(str(item) for item in payload["tools_requested"])
    return (
        list(dict.fromkeys(source_types)),
        list(dict.fromkeys(requested)),
        list(dict.fromkeys(executed)),
        list(dict.fromkeys(errors)),
    )


class LLMAgentTeamRunner:
    """Run routed LLM Agents while Python owns tools, scores, and permissions."""

    def __init__(
        self,
        *,
        provider: AgentProvider,
        mode: RunMode,
        trace: TraceRecorder,
        tool_executor: SignalToolExecutor | None = None,
        tool_limits: AgentLoopLimits | None = None,
        loop_limits: AgentLoopLimits | None = None,
    ) -> None:
        if not mode.uses_llm_agent_path:
            raise ValueError("LLMAgentTeamRunner requires mock-agent or agent mode")
        self.provider = provider
        self.mode = mode
        self.trace = trace
        self.tool_executor = tool_executor
        self.loop_limits = loop_limits or tool_limits or AgentLoopLimits()
        self.tool_limits = self.loop_limits
        self._tool_requests_used = 0
        self.tool_cache = ToolObservationCache()
        self.supervisor = SignalSupervisorAgent()
        self.evidence = ContextEvidenceAgent()
        self.impact = ImpactAnalystAgent()
        self.action = ActionPlannerAgent()
        self.learning = LearningPolicyAgent()

    async def _invoke(
        self,
        call: AgentCall,
        output_model: type[OutputT],
        fallback: Callable[[], OutputT],
        *,
        event_ids: Iterable[str],
    ) -> tuple[OutputT, int]:
        started = time.perf_counter()
        schema_valid = False
        fallback_used = False
        error: str | None = None
        schema_error: str | None = None
        retry_count = 0

        def parse_response(response: str) -> OutputT:
            return output_model.model_validate(_json_object(response))

        async def provider_text(target: AgentCall) -> str:
            try:
                return await asyncio.wait_for(
                    self.provider.complete(target),
                    timeout=self.loop_limits.max_agent_call_seconds,
                )
            except TimeoutError as exc:
                raise TimeoutError(
                    f"provider_timeout after "
                    f"{self.loop_limits.max_agent_call_seconds}s"
                ) from exc

        try:
            output = parse_response(await provider_text(call))
            schema_valid = True
        except TimeoutError as exc:
            schema_error = _short_error(exc)
            error = schema_error
            fallback_used = True
            output = fallback()
        except Exception as exc:
            schema_error = _short_error(exc)
            if self.loop_limits.max_schema_retries <= 0:
                fallback_used = True
                output = fallback()
                error = schema_error
            else:
                retry_count = 1
                retry_call = replace(
                    call,
                    user_prompt=(
                        call.user_prompt.rstrip()
                        + "\n\n"
                        + "Your previous response failed JSON/schema validation: "
                        + schema_error
                        + ". Return exactly one valid JSON object matching the required "
                        + "schema. Do not include Markdown."
                    ),
                )
                try:
                    output = parse_response(await provider_text(retry_call))
                    schema_valid = True
                except Exception as retry_exc:
                    retry_error = _short_error(retry_exc)
                    schema_error = f"{schema_error}; retry failed: {retry_error}"
                    error = schema_error
                    fallback_used = True
                    output = fallback()
        duration_ms = max(0, round((time.perf_counter() - started) * 1000))
        source_types, requested, executed, tool_errors = _trace_tools(output)
        index = append_llm_trace(
            self.trace,
            call=call,
            provider=self.provider,
            mode=self.mode,
            input_event_id=",".join(event_ids) or "memory",
            input_count=call.input_count,
            output_count=_output_count(output),
            duration_ms=duration_ms,
            schema_valid=schema_valid,
            fallback_used=fallback_used,
            source_types_observed=source_types,
            tools_requested=requested,
            tools_executed=executed,
            tool_errors=tool_errors,
            retry_count=retry_count,
            schema_error=schema_error,
            error=error,
        )
        return output, index

    def _ensure_coverage(
        self,
        output: OutputT,
        *,
        expected_ids: set[str],
        fallback: Callable[[], OutputT],
        trace_index: int,
    ) -> OutputT:
        raw = output.model_dump(mode="json")
        raw_results = raw.get("results", raw.get("routes"))
        observed = {
            str(item.get("event_id"))
            for item in raw_results or []
            if isinstance(item, dict)
        }
        if observed == expected_ids:
            return output
        replacement = fallback()
        trace = self.trace.steps[trace_index]
        self.trace.steps[trace_index] = trace.model_copy(
            update={
                "schema_valid": False,
                "fallback_used": True,
                "output_count": _output_count(replacement),
                "error": "Agent output did not cover every input event exactly once.",
                "detail": "Coverage validation failed; deterministic fallback used.",
            }
        )
        return replacement

    async def run_scan(
        self,
        events: list[SignalEvent],
        *,
        project_profile: dict[str, Any],
        policy: dict[str, Any],
        memory_snapshot: dict[str, Any],
        clusters: list[SignalCluster] | None = None,
        noise_assessments: list[NoiseAssessment] | None = None,
        failed_sources: list[str] | None = None,
        run_id: str = "",
        seen_hashes: set[str] | None = None,
        feedback_history: Iterable[FeedbackRecord] = (),
    ) -> tuple[list[SignalAssessment], LearningPolicyOutput]:
        event_ids = [event.event_id for event in events]
        expected_ids = set(event_ids)
        active_clusters = clusters or []
        active_noise = noise_assessments or []
        volatile = {
            "run_id": run_id,
            "provider": self.provider.name,
            "model": self.provider.model,
            "failed_sources": failed_sources or [],
        }
        supervisor_call = self.supervisor.build_call(
            events,
            project_profile,
            policy=policy,
            noise_assessments=active_noise,
            clusters=active_clusters,
            volatile_metadata=volatile,
        )
        routes, route_trace = await self._invoke(
            supervisor_call,
            SupervisorOutput,
            lambda: self.supervisor.fallback(
                events,
                project_profile,
                active_noise,
                active_clusters,
            ),
            event_ids=event_ids,
        )
        routes = self._ensure_coverage(
            routes,
            expected_ids=expected_ids,
            fallback=lambda: self.supervisor.fallback(
                events,
                project_profile,
                active_noise,
                active_clusters,
            ),
            trace_index=route_trace,
        )
        route_by_id = {route.event_id: route for route in routes.routes}

        evidence_events = self._events_for(events, route_by_id, "context_evidence")
        evidence_ids = {event.event_id for event in evidence_events}
        if evidence_events:
            plan_call = self.evidence.build_tool_plan_call(
                evidence_events,
                routes,
                project_profile=project_profile,
                policy=policy,
                clusters=active_clusters,
                memory=memory_snapshot,
                volatile_metadata=volatile,
            )
            plan, plan_trace = await self._invoke(
                plan_call,
                EvidenceToolPlan,
                lambda: self.evidence.fallback_plan(evidence_events),
                event_ids=[event.event_id for event in evidence_events],
            )
            observations, tool_trace = await self._execute_tool_requests(
                plan,
                policy,
                event_count=len(evidence_events),
            )
            plan_step = self.trace.steps[plan_trace]
            self.trace.steps[plan_trace] = plan_step.model_copy(
                update={
                    "tools_executed": tool_trace["executed"],
                    "tool_errors": tool_trace["errors"],
                    "blocked_tools": tool_trace["blocked"],
                    "budget_blocked_count": len(tool_trace["budget_blocked"]),
                    "permission_checks": tool_trace["permission_checks"],
                    "cache_events": tool_trace["cache_events"],
                }
            )
            evidence_call = self.evidence.build_final_call(
                evidence_events,
                routes,
                plan,
                observations,
                project_profile=project_profile,
                policy=policy,
                clusters=active_clusters,
                memory=memory_snapshot,
                volatile_metadata=volatile,
            )
            evidence, evidence_trace = await self._invoke(
                evidence_call,
                ContextEvidenceOutput,
                lambda: self.evidence.fallback(
                    evidence_events,
                    plan=plan,
                    observations=observations,
                ),
                event_ids=[event.event_id for event in evidence_events],
            )
            evidence = self._ensure_coverage(
                evidence,
                expected_ids=evidence_ids,
                fallback=lambda: self.evidence.fallback(
                    evidence_events,
                    plan=plan,
                    observations=observations,
                ),
                trace_index=evidence_trace,
            )
            evidence = self._cap_evidence_after_tool_failures(evidence, observations)
            final_step = self.trace.steps[evidence_trace]
            exit_condition = self._evidence_exit_condition(
                plan,
                observations,
            )
            self.trace.steps[evidence_trace] = final_step.model_copy(
                update={
                    "tools_executed": tool_trace["executed"],
                    "tool_errors": tool_trace["errors"],
                    "blocked_tools": tool_trace["blocked"],
                    "permission_checks": tool_trace["permission_checks"],
                    "cache_events": tool_trace["cache_events"],
                    "event_input_count": len(evidence_events),
                    "tool_observation_count": len(observations),
                    "source_type_count": len(
                        {event.source_type for event in evidence_events}
                    ),
                    "tools_requested_count": len(plan.tool_requests),
                    "tools_executed_count": len(tool_trace["executed"]),
                    "budget_blocked_count": len(tool_trace["budget_blocked"]),
                    "exit_condition": exit_condition,
                }
            )
        else:
            evidence = ContextEvidenceOutput(results=[])
        skipped_evidence = [
            event for event in events if event.event_id not in evidence_ids
        ]
        if skipped_evidence:
            evidence = evidence.model_copy(
                update={
                    "results": [
                        *evidence.results,
                        *self.evidence.fallback(skipped_evidence).results,
                    ]
                }
            )

        impact_events = self._events_for(events, route_by_id, "impact")
        impact_ids = {event.event_id for event in impact_events}
        if impact_events:
            impact_evidence = ContextEvidenceOutput(
                results=[
                    item for item in evidence.results if item.event_id in impact_ids
                ]
            )
            impact_call = self.impact.build_call(
                impact_events,
                project_profile,
                routes,
                impact_evidence,
                clusters=active_clusters,
                policy=policy,
                volatile_metadata=volatile,
            )
            impact, impact_trace = await self._invoke(
                impact_call,
                ImpactOutput,
                lambda: self.impact.fallback(
                    impact_events,
                    project_profile,
                    policy,
                    active_clusters,
                ),
                event_ids=[event.event_id for event in impact_events],
            )
            impact = self._ensure_coverage(
                impact,
                expected_ids=impact_ids,
                fallback=lambda: self.impact.fallback(
                    impact_events,
                    project_profile,
                    policy,
                    active_clusters,
                ),
                trace_index=impact_trace,
            )
        else:
            impact = ImpactOutput(results=[])
        skipped_impact = [event for event in events if event.event_id not in impact_ids]
        if skipped_impact:
            impact = impact.model_copy(
                update={
                    "results": [
                        *impact.results,
                        *self.impact.fallback(
                            skipped_impact,
                            project_profile,
                            policy,
                            active_clusters,
                        ).results,
                    ]
                }
            )

        action_events = self._events_for(events, route_by_id, "action")
        action_ids = {event.event_id for event in action_events}
        if action_events:
            action_impact = ImpactOutput(
                results=[
                    item for item in impact.results if item.event_id in action_ids
                ]
            )
            action_call = self.action.build_call(
                action_events,
                action_impact,
                project_profile=project_profile,
                policy=policy,
                volatile_metadata=volatile,
            )
            action, action_trace = await self._invoke(
                action_call,
                ActionOutput,
                lambda: self.action.fallback(action_events, action_impact),
                event_ids=[event.event_id for event in action_events],
            )
            action = self._ensure_coverage(
                action,
                expected_ids=action_ids,
                fallback=lambda: self.action.fallback(action_events, action_impact),
                trace_index=action_trace,
            )
        else:
            action = ActionOutput(results=[])
            action_trace = None
        skipped_action = [event for event in events if event.event_id not in action_ids]
        if skipped_action:
            skipped_ids = {event.event_id for event in skipped_action}
            skipped_impact_output = ImpactOutput(
                results=[
                    item for item in impact.results if item.event_id in skipped_ids
                ]
            )
            action = action.model_copy(
                update={
                    "results": [
                        *action.results,
                        *self.action.fallback(
                            skipped_action,
                            skipped_impact_output,
                        ).results,
                    ]
                }
            )

        audit_fallback_event_ids = [
            event.event_id
            for event in events
            if not route_by_id[event.event_id].analyze
            or any(
                stage not in route_by_id[event.event_id].required_agents
                for stage in ("context_evidence", "impact", "action")
            )
        ]
        if audit_fallback_event_ids:
            self.trace.steps.append(
                TraceStep(
                    step="skipped_event_audit_fallback",
                    status="success",
                    agent="DeterministicAuditFallback",
                    input_count=len(audit_fallback_event_ids),
                    output_count=len(audit_fallback_event_ids),
                    duration_ms=0,
                    fallback_used=True,
                    detail=(
                        "Supervisor routing skipped one or more downstream LLM stages. "
                        "Deterministic fallback generated complete audit assessments only; "
                        "this is not downstream LLM Agent execution. Events: "
                        + ", ".join(audit_fallback_event_ids)
                    ),
                )
            )

        assessments, permission_checks = self._guarded_assessments(
            events,
            routes=routes,
            evidence=evidence,
            impact=impact,
            action=action,
            project_profile=project_profile,
            policy=policy,
            noise_assessments=active_noise,
            seen_hashes=seen_hashes,
            feedback_history=feedback_history,
        )
        if action_trace is not None:
            action_step = self.trace.steps[action_trace]
            self.trace.steps[action_trace] = action_step.model_copy(
                update={"permission_checks": permission_checks}
            )

        needs_learning = any(
            "learning_observation" in route.required_agents for route in routes.routes
        )
        learning_memories = {
            **memory_snapshot,
            "current_signal_batch": [
                event.model_dump(mode="json") for event in events
            ],
            "current_assessments": [
                assessment.model_dump(mode="json") for assessment in assessments
            ],
        }
        if needs_learning:
            learning_call = self.learning.build_call(learning_memories)
            learning, _ = await self._invoke(
                learning_call,
                LearningPolicyOutput,
                lambda: self.learning.fallback(learning_memories),
                event_ids=event_ids,
            )
        else:
            learning = self.learning.fallback(learning_memories)
        return assessments, learning

    async def run_learning(
        self,
        memory_snapshot: dict[str, Any],
    ) -> LearningPolicyOutput:
        call = self.learning.build_call(memory_snapshot)
        output, _ = await self._invoke(
            call,
            LearningPolicyOutput,
            lambda: self.learning.fallback(memory_snapshot),
            event_ids=[],
        )
        return output

    @staticmethod
    def _events_for(
        events: list[SignalEvent],
        routes: dict[str, Any],
        agent: str,
    ) -> list[SignalEvent]:
        return [
            event
            for event in events
            if routes[event.event_id].analyze
            and agent in routes[event.event_id].required_agents
        ]

    async def _execute_tool_requests(
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
        max_for_events = max(1, event_count) * self.tool_limits.max_tool_requests_per_event
        remaining_for_run = max(
            0,
            self.tool_limits.max_total_tool_requests_per_run - self._tool_requests_used,
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
                        output_summary="Tool request skipped because the tool budget was exceeded.",
                        error="budget_exceeded",
                    )
                )
                continue
            self._tool_requests_used += 1
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
            or not self._request_is_read_only(name, request.arguments)
        ):
            trace["blocked"].append(name)
            trace["permission_checks"].append(f"{name}:blocked:not-read-only-allowlisted")
            return ToolObservation(
                tool_name=name,
                status="blocked",
                output_summary="Tool request was blocked by the read-only allowlist.",
                error=f"{name} is not an allowed evidence tool",
            )
        permission_action = self._permission_action(name, request.arguments)
        decision = guard.evaluate(permission_action)
        trace["permission_checks"].append(
            f"{name}:{permission_action}:{'allowed' if decision.allowed else 'blocked'}"
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
        output_limit = self.tool_limits.max_tool_output_chars
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

    @staticmethod
    def _permission_action(name: str, arguments: dict[str, Any]) -> str:
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

    @staticmethod
    def _request_is_read_only(name: str, arguments: dict[str, Any]) -> bool:
        if name == "signal_memory":
            return str(arguments.get("action", "")).startswith("load_")
        return name in READ_ONLY_TOOL_ALLOWLIST

    @staticmethod
    def _evidence_exit_condition(
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

    @staticmethod
    def _cap_evidence_after_tool_failures(
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
                        "tool_errors": list(
                            dict.fromkeys([*item.tool_errors, message])
                        ),
                    }
                )
                for item in evidence.results
            ]
        )

    def _guarded_assessments(
        self,
        events: list[SignalEvent],
        *,
        routes: SupervisorOutput,
        evidence: ContextEvidenceOutput,
        impact: ImpactOutput,
        action: ActionOutput,
        project_profile: dict[str, Any],
        policy: dict[str, Any],
        noise_assessments: list[NoiseAssessment],
        seen_hashes: set[str] | None,
        feedback_history: Iterable[FeedbackRecord],
    ) -> tuple[list[SignalAssessment], list[str]]:
        route_by_id = {item.event_id: item for item in routes.routes}
        evidence_by_id = {item.event_id: item for item in evidence.results}
        impact_by_id = {item.event_id: item for item in impact.results}
        action_by_id = {item.event_id: item for item in action.results}
        noise_by_id = {item.event_id: item for item in noise_assessments}
        guard = SignalPermissionGuard(policy)
        permission_checks: list[str] = []
        assessments: list[SignalAssessment] = []
        feedback = list(feedback_history)
        weights = policy.get("agent_score_weights", {})
        deterministic_weight = float(weights.get("deterministic_base", 0.70))
        semantic_weight = float(weights.get("semantic_relevance", 0.20))
        evidence_weight = float(weights.get("evidence_confidence", 0.10))
        total = deterministic_weight + semantic_weight + evidence_weight
        if total <= 0:
            deterministic_weight, semantic_weight, evidence_weight, total = 0.7, 0.2, 0.1, 1
        deterministic_weight /= total
        semantic_weight /= total
        evidence_weight /= total

        for event in events:
            route = route_by_id[event.event_id]
            evidence_item = evidence_by_id[event.event_id]
            impact_item = impact_by_id[event.event_id]
            action_item = action_by_id[event.event_id]
            noise = noise_by_id.get(event.event_id)
            base = score_signal(
                event,
                project_profile,
                policy,
                seen_hashes=seen_hashes,
                feedback_history=feedback,
                category=route.category,
            )
            configured_category = float(
                policy.get("category_weights", {}).get(route.category.value, 1.0)
            )
            policy_multiplier = (
                0.10
                if route.category is SignalCategory.NOISE
                else max(0.85, min(1.0, 0.85 + 0.15 * configured_category))
            )
            if noise is not None:
                policy_multiplier *= noise.score_multiplier
            blended = (
                base.final_score * deterministic_weight
                + impact_item.semantic_relevance * semantic_weight
                + evidence_item.confidence * 100 * evidence_weight
            )
            final_score = round(max(0.0, min(100.0, blended * policy_multiplier)), 2)
            decision = decision_for_score(final_score, policy)
            if route.category is SignalCategory.NOISE or not route.analyze:
                decision = SignalDecision.IGNORE

            approval_notes: list[str] = []
            if route.category is SignalCategory.POLICY_SIGNAL:
                action_item = action_item.model_copy(update={"approval_required": True})
            if (
                "action" in route.required_agents
                and not action_item.requested_actions
            ):
                permission_checks.append(
                    f"{event.event_id}:no-high-risk-actions-requested"
                )
            for requested in action_item.requested_actions:
                permission = guard.evaluate(requested)
                permission_checks.append(
                    f"{event.event_id}:{requested}:"
                    f"{'allowed' if permission.allowed else 'blocked'}"
                )
                if not permission.allowed:
                    approval_notes.append(
                        f"Approval required before `{requested}`: {permission.reason}"
                    )
            action_items = list(
                dict.fromkeys([*action_item.action_items, *approval_notes])
            )
            if action_item.approval_required and action_items:
                action_items.append("Human approval is required before execution.")
            if decision is SignalDecision.IGNORE:
                action_items = []

            agent_score = AgentScoreBreakdown(
                deterministic_base_score=base.final_score,
                semantic_relevance=impact_item.semantic_relevance,
                evidence_confidence_score=round(evidence_item.confidence * 100, 2),
                deterministic_weight=round(deterministic_weight, 4),
                semantic_weight=round(semantic_weight, 4),
                evidence_weight=round(evidence_weight, 4),
                policy_multiplier=round(max(0.0, min(1.0, policy_multiplier)), 4),
                final_score=final_score,
            )
            assessments.append(
                SignalAssessment(
                    event_id=event.event_id,
                    category=route.category,
                    relevance_score=impact_item.semantic_relevance,
                    impact_score=final_score,
                    confidence=evidence_item.confidence,
                    affected_modules=impact_item.affected_modules,
                    evidence_urls=evidence_item.evidence_urls,
                    source_quality=evidence_item.source_quality,
                    reason=" ".join(
                        filter(
                            None,
                            [
                                route.routing_reason,
                                route.noise_reason,
                                evidence_item.context_summary,
                                evidence_item.uncertainty,
                                impact_item.impact_reason,
                                action_item.critic_notes,
                            ],
                        )
                    ),
                    action_items=action_items,
                    decision=decision,
                    score_breakdown=base,
                    agent_score_breakdown=agent_score,
                    related_event_ids=impact_item.related_event_ids,
                    cross_source_confidence=impact_item.cross_source_confidence,
                    conflicting_evidence=impact_item.conflicting_evidence,
                    related_cluster_id=route.related_cluster_id,
                    noise_reason=route.noise_reason or (noise.noise_reason if noise else None),
                    required_agents=list(route.required_agents),
                )
            )
        return assessments, permission_checks
