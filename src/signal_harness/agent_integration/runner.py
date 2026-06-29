"""Five-Agent execution with controlled tools and deterministic safety."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Any, TypeVar

from pydantic import BaseModel

from signal_harness.agent_integration.invoker import AgentInvoker, _output_count
from signal_harness.agent_integration.mode import RunMode
from signal_harness.agent_integration.repair import (
    RepairCoordinator,
    merge_action,
    merge_context_evidence,
    merge_impact,
    repair_metadata,
)
from signal_harness.agent_integration.schemas import (
    ActionOutput,
    ContextEvidenceOutput,
    EvidenceToolPlan,
    ImpactOutput,
    LearningPolicyOutput,
    SupervisorOutput,
    ToolObservation,
)
from signal_harness.agent_integration.scoring_bridge import guarded_assessments
from signal_harness.agent_integration.tool_loop import (
    ControlledToolLoop,
    ToolExecutionTrace,
    cap_evidence_after_tool_failures,
    evidence_exit_condition,
)
from signal_harness.agent_team import (
    ActionPlannerAgent,
    ContextEvidenceAgent,
    ImpactAnalystAgent,
    LearningPolicyAgent,
    SignalSupervisorAgent,
)
from signal_harness.providers.adapter import AgentCall, AgentProvider
from signal_harness.runtime.tool_executor import SignalToolExecutor
from signal_harness.runtime.tracing import TraceRecorder
from signal_harness.signal.schemas import (
    FeedbackRecord,
    NoiseAssessment,
    SignalAssessment,
    SignalCluster,
    SignalEvent,
    TraceStep,
)

OutputT = TypeVar("OutputT", bound=BaseModel)


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
        self.invoker = AgentInvoker(
            provider=self.provider,
            mode=self.mode,
            trace=self.trace,
            limits=self.loop_limits,
        )
        self.tool_loop = ControlledToolLoop(
            tool_executor=self.tool_executor,
            limits=self.tool_limits,
        )
        self.repair = RepairCoordinator(
            trace=self.trace,
            max_repair_rounds_per_run=self.loop_limits.max_repair_rounds_per_run,
            max_repair_events_per_run=self.loop_limits.max_repair_events_per_run,
        )
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
        return await self.invoker.invoke(
            call,
            output_model,
            fallback,
            event_ids=event_ids,
        )

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
            evidence, impact = await self._maybe_run_impact_evidence_repair(
                events=impact_events,
                routes=routes,
                evidence=evidence,
                impact=impact,
                project_profile=project_profile,
                policy=policy,
                clusters=active_clusters,
                memory_snapshot=memory_snapshot,
                volatile_metadata=volatile,
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
            impact, action = await self._maybe_run_action_impact_repair(
                events=action_events,
                routes=routes,
                evidence=evidence,
                impact=impact,
                action=action,
                project_profile=project_profile,
                policy=policy,
                clusters=active_clusters,
                volatile_metadata=volatile,
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

        assessments, permission_checks = guarded_assessments(
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
        return await self.tool_loop.execute_tool_requests(
            plan,
            policy,
            event_count=event_count,
        )

    @staticmethod
    def _evidence_exit_condition(
        plan: EvidenceToolPlan,
        observations: list[ToolObservation],
    ) -> str:
        return evidence_exit_condition(plan, observations)

    @staticmethod
    def _cap_evidence_after_tool_failures(
        evidence: ContextEvidenceOutput,
        observations: list[ToolObservation],
    ) -> ContextEvidenceOutput:
        return cap_evidence_after_tool_failures(evidence, observations)

    async def _maybe_run_impact_evidence_repair(
        self,
        *,
        events: list[SignalEvent],
        routes: SupervisorOutput,
        evidence: ContextEvidenceOutput,
        impact: ImpactOutput,
        project_profile: dict[str, Any],
        policy: dict[str, Any],
        clusters: list[SignalCluster],
        memory_snapshot: dict[str, Any],
        volatile_metadata: dict[str, Any],
    ) -> tuple[ContextEvidenceOutput, ImpactOutput]:
        event_ids, reason = self.repair.impact_to_evidence_repair_candidates(
            events=events,
            evidence=evidence,
            impact=impact,
        )
        if not event_ids:
            return evidence, impact
        repair_round = self.repair.next_repair_round()
        event_ids = self.repair.prepare_repair_event_ids(
            event_ids,
            valid_ids={event.event_id for event in events},
            triggered_by="ImpactAnalystAgent",
            target_agent="context_evidence",
            repair_round=repair_round,
        )
        if not event_ids:
            return evidence, impact
        self.repair.append_repair_requested(
            triggered_by="ImpactAnalystAgent",
            target_agent="context_evidence",
            event_ids=event_ids,
            reason=reason,
            severity="high",
            repair_round=repair_round,
            summary_step="repair_context_evidence",
        )
        if not self.repair.reserve_repair_round(
            triggered_by="ImpactAnalystAgent",
            target_agent="context_evidence",
            event_ids=event_ids,
            repair_round=repair_round,
        ):
            return evidence, impact

        repair_events = [event for event in events if event.event_id in set(event_ids)]
        plan_call = self.evidence.build_tool_plan_call(
            repair_events,
            routes,
            project_profile=project_profile,
            policy=policy,
            clusters=clusters,
            memory=memory_snapshot,
            volatile_metadata={
                **volatile_metadata,
                "repair_pass": "impact_to_context_evidence",
            },
        )
        plan, plan_trace = await self._invoke(
            plan_call,
            EvidenceToolPlan,
            lambda: self.evidence.fallback_plan(repair_events),
            event_ids=event_ids,
        )
        observations, tool_trace = await self._execute_tool_requests(
            plan,
            policy,
            event_count=len(repair_events),
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
                "detail": (
                    "repair_internal_llm_call=true; "
                    "repair_phase=impact_to_context_evidence_plan; "
                    "summary_step=repair_context_evidence"
                ),
                "metadata": repair_metadata(
                    event_ids=event_ids,
                    repair_round=repair_round,
                    summary_step="repair_context_evidence",
                    internal_llm_call=True,
                    phase="impact_to_context_evidence_plan",
                ),
            }
        )
        evidence_call = self.evidence.build_final_call(
            repair_events,
            routes,
            plan,
            observations,
            project_profile=project_profile,
            policy=policy,
            clusters=clusters,
            memory=memory_snapshot,
            volatile_metadata={
                **volatile_metadata,
                "repair_pass": "impact_to_context_evidence",
            },
        )
        repaired_evidence, evidence_trace = await self._invoke(
            evidence_call,
            ContextEvidenceOutput,
            lambda: self.evidence.fallback(
                repair_events,
                plan=plan,
                observations=observations,
            ),
            event_ids=event_ids,
        )
        expected_ids = {event.event_id for event in repair_events}
        repaired_evidence = self._ensure_coverage(
            repaired_evidence,
            expected_ids=expected_ids,
            fallback=lambda: self.evidence.fallback(
                repair_events,
                plan=plan,
                observations=observations,
            ),
            trace_index=evidence_trace,
        )
        repaired_evidence = self._cap_evidence_after_tool_failures(
            repaired_evidence,
            observations,
        )
        final_step = self.trace.steps[evidence_trace]
        self.trace.steps[evidence_trace] = final_step.model_copy(
            update={
                "tools_executed": tool_trace["executed"],
                "tool_errors": tool_trace["errors"],
                "blocked_tools": tool_trace["blocked"],
                "permission_checks": tool_trace["permission_checks"],
                "cache_events": tool_trace["cache_events"],
                "event_input_count": len(repair_events),
                "tool_observation_count": len(observations),
                "source_type_count": len({event.source_type for event in repair_events}),
                "tools_requested_count": len(plan.tool_requests),
                "tools_executed_count": len(tool_trace["executed"]),
                "budget_blocked_count": len(tool_trace["budget_blocked"]),
                "exit_condition": self._evidence_exit_condition(plan, observations),
                "detail": (
                    "repair_internal_llm_call=true; "
                    "repair_phase=impact_to_context_evidence_final; "
                    "summary_step=repair_context_evidence"
                ),
                "metadata": repair_metadata(
                    event_ids=event_ids,
                    repair_round=repair_round,
                    summary_step="repair_context_evidence",
                    internal_llm_call=True,
                    phase="impact_to_context_evidence_final",
                ),
            }
        )
        merged_evidence = merge_context_evidence(evidence, repaired_evidence)
        repaired_impact_input = ContextEvidenceOutput(
            results=[
                item
                for item in merged_evidence.results
                if item.event_id in expected_ids
            ]
        )
        impact_call = self.impact.build_call(
            repair_events,
            project_profile,
            routes,
            repaired_impact_input,
            clusters=clusters,
            policy=policy,
            volatile_metadata={
                **volatile_metadata,
                "repair_pass": "context_evidence_to_impact",
            },
        )
        repaired_impact, impact_trace = await self._invoke(
            impact_call,
            ImpactOutput,
            lambda: self.impact.fallback(
                repair_events,
                project_profile,
                policy,
                clusters,
            ),
            event_ids=event_ids,
        )
        repaired_impact = self._ensure_coverage(
            repaired_impact,
            expected_ids=expected_ids,
            fallback=lambda: self.impact.fallback(
                repair_events,
                project_profile,
                policy,
                clusters,
            ),
            trace_index=impact_trace,
        )
        self.repair.mark_repair_llm_trace(
            impact_trace,
            phase="context_evidence_to_impact",
            summary_step="repair_impact",
            event_ids=event_ids,
            repair_round=repair_round,
        )
        merged_impact = merge_impact(impact, repaired_impact)
        self.trace.steps.append(
            TraceStep(
                step="repair_context_evidence",
                status="success",
                agent="RepairCoordinator",
                input_count=len(event_ids),
                output_count=len(repaired_evidence.results),
                duration_ms=0,
                detail=(
                    "Executed bounded Impact→ContextEvidence repair; "
                    f"event_ids={','.join(event_ids)}; reason={reason}"
                ),
                metadata=repair_metadata(
                    triggered_by="ImpactAnalystAgent",
                    target_agent="context_evidence",
                    event_ids=event_ids,
                    reason=reason,
                    severity="high",
                    repair_round=repair_round,
                    summary_step="repair_context_evidence",
                ),
                tools_requested=[
                    request.tool_name for request in plan.tool_requests
                ],
                tools_executed=tool_trace["executed"],
                tool_errors=tool_trace["errors"],
                blocked_tools=tool_trace["blocked"],
                budget_blocked_count=len(tool_trace["budget_blocked"]),
                permission_checks=tool_trace["permission_checks"],
                event_input_count=len(repair_events),
                tool_observation_count=len(observations),
                tools_requested_count=len(plan.tool_requests),
                tools_executed_count=len(tool_trace["executed"]),
                exit_condition=self._evidence_exit_condition(plan, observations),
            )
        )
        self.trace.steps.append(
            TraceStep(
                step="repair_impact",
                status="success",
                agent="RepairCoordinator",
                input_count=len(event_ids),
                output_count=len(repaired_impact.results),
                duration_ms=0,
                detail=(
                    "Reran ImpactAnalystAgent after evidence repair; "
                    f"event_ids={','.join(event_ids)}"
                ),
                metadata=repair_metadata(
                    triggered_by="ContextEvidenceAgent",
                    target_agent="impact",
                    event_ids=event_ids,
                    reason="Reran ImpactAnalystAgent after evidence repair.",
                    repair_round=repair_round,
                    summary_step="repair_impact",
                ),
            )
        )
        return merged_evidence, merged_impact

    async def _maybe_run_action_impact_repair(
        self,
        *,
        events: list[SignalEvent],
        routes: SupervisorOutput,
        evidence: ContextEvidenceOutput,
        impact: ImpactOutput,
        action: ActionOutput,
        project_profile: dict[str, Any],
        policy: dict[str, Any],
        clusters: list[SignalCluster],
        volatile_metadata: dict[str, Any],
    ) -> tuple[ImpactOutput, ActionOutput]:
        event_ids, reason = self.repair.action_to_impact_repair_candidates(
            events=events,
            impact=impact,
            action=action,
        )
        if not event_ids:
            return impact, action
        repair_round = self.repair.next_repair_round()
        event_ids = self.repair.prepare_repair_event_ids(
            event_ids,
            valid_ids={event.event_id for event in events},
            triggered_by="ActionPlannerAgent",
            target_agent="impact",
            repair_round=repair_round,
        )
        if not event_ids:
            return impact, action
        self.repair.append_repair_requested(
            triggered_by="ActionPlannerAgent",
            target_agent="impact",
            event_ids=event_ids,
            reason=reason,
            severity="medium",
            repair_round=repair_round,
            summary_step="repair_impact",
        )
        if not self.repair.reserve_repair_round(
            triggered_by="ActionPlannerAgent",
            target_agent="impact",
            event_ids=event_ids,
            repair_round=repair_round,
        ):
            return impact, action

        event_id_set = set(event_ids)
        repair_events = [event for event in events if event.event_id in event_id_set]
        repair_evidence = ContextEvidenceOutput(
            results=[
                item for item in evidence.results if item.event_id in event_id_set
            ]
        )
        impact_call = self.impact.build_call(
            repair_events,
            project_profile,
            routes,
            repair_evidence,
            clusters=clusters,
            policy=policy,
            volatile_metadata={
                **volatile_metadata,
                "repair_pass": "action_to_impact",
            },
        )
        repaired_impact, impact_trace = await self._invoke(
            impact_call,
            ImpactOutput,
            lambda: self.impact.fallback(
                repair_events,
                project_profile,
                policy,
                clusters,
            ),
            event_ids=event_ids,
        )
        repaired_impact = self._ensure_coverage(
            repaired_impact,
            expected_ids=event_id_set,
            fallback=lambda: self.impact.fallback(
                repair_events,
                project_profile,
                policy,
                clusters,
            ),
            trace_index=impact_trace,
        )
        self.repair.mark_repair_llm_trace(
            impact_trace,
            phase="action_to_impact",
            summary_step="repair_impact",
            event_ids=event_ids,
            repair_round=repair_round,
        )
        merged_impact = merge_impact(impact, repaired_impact)
        action_impact = ImpactOutput(results=repaired_impact.results)
        action_call = self.action.build_call(
            repair_events,
            action_impact,
            project_profile=project_profile,
            policy=policy,
            volatile_metadata={
                **volatile_metadata,
                "repair_pass": "impact_to_action",
            },
        )
        repaired_action, action_trace = await self._invoke(
            action_call,
            ActionOutput,
            lambda: self.action.fallback(repair_events, action_impact),
            event_ids=event_ids,
        )
        repaired_action = self._ensure_coverage(
            repaired_action,
            expected_ids=event_id_set,
            fallback=lambda: self.action.fallback(repair_events, action_impact),
            trace_index=action_trace,
        )
        self.repair.mark_repair_llm_trace(
            action_trace,
            phase="impact_to_action",
            summary_step="repair_action",
            event_ids=event_ids,
            repair_round=repair_round,
        )
        merged_action = merge_action(action, repaired_action)
        self.trace.steps.append(
            TraceStep(
                step="repair_impact",
                status="success",
                agent="RepairCoordinator",
                input_count=len(event_ids),
                output_count=len(repaired_impact.results),
                duration_ms=0,
                detail=(
                    "Executed bounded Action→Impact repair; "
                    f"event_ids={','.join(event_ids)}; reason={reason}"
                ),
                metadata=repair_metadata(
                    triggered_by="ActionPlannerAgent",
                    target_agent="impact",
                    event_ids=event_ids,
                    reason=reason,
                    severity="medium",
                    repair_round=repair_round,
                    summary_step="repair_impact",
                ),
            )
        )
        self.trace.steps.append(
            TraceStep(
                step="repair_action",
                status="success",
                agent="RepairCoordinator",
                input_count=len(event_ids),
                output_count=len(repaired_action.results),
                duration_ms=0,
                detail=(
                    "Reran ActionPlannerAgent after impact repair; "
                    f"event_ids={','.join(event_ids)}"
                ),
                metadata=repair_metadata(
                    triggered_by="ImpactAnalystAgent",
                    target_agent="action",
                    event_ids=event_ids,
                    reason="Reran ActionPlannerAgent after impact repair.",
                    repair_round=repair_round,
                    summary_step="repair_action",
                ),
            )
        )
        return merged_impact, merged_action
