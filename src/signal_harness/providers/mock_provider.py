"""Offline scripted provider for the real SignalHarness Agent path."""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Literal
from uuid import uuid4

from signal_harness.agent_integration.schemas import (
    ActionItem,
    ActionOutput,
    ContextEvidenceItem,
    ContextEvidenceOutput,
    EvidenceToolPlan,
    ImpactItem,
    ImpactOutput,
    LearningPolicyOutput,
    RequiredAgent,
    SupervisorOutput,
    SupervisorRoute,
    ToolObservation,
    ToolRequest,
)
from signal_harness.agent_team import (
    ActionPlannerAgent,
    ContextEvidenceAgent,
    ImpactAnalystAgent,
    LearningPolicyAgent,
    SignalSupervisorAgent,
)
from signal_harness.providers.adapter import AgentCall
from signal_harness.signal.schemas import (
    PolicyUpdateProposal,
    SignalCategory,
    SignalCluster,
    SignalEvent,
    SourceQuality,
)


class MockProvider:
    """Return LLM-like fixtures without using fallback unless requested."""

    name = "mock-provider"
    model = "mock-signal-model-v2"

    def __init__(
        self,
        *,
        strategy: Literal["scripted", "fallback"] = "scripted",
        invalid_agents: set[str] | None = None,
        responses: dict[str, str] | None = None,
    ) -> None:
        self.strategy = strategy
        self.invalid_agents = invalid_agents or set()
        self.responses = responses or {}
        self.calls: list[AgentCall] = []

    async def complete(self, call: AgentCall) -> str:
        self.calls.append(call)
        response = self.responses.get(call.output_schema)
        if response is None:
            response = self.responses.get(call.agent_name)
        if response is not None:
            return response
        if call.agent_name in self.invalid_agents:
            return "{invalid-json"
        if self.strategy == "fallback":
            return self._fallback_response(call)
        return self._scripted_response(call)

    def _scripted_response(self, call: AgentCall) -> str:
        payload = call.input_payload
        if call.output_schema == "SupervisorOutput":
            return self._scripted_supervisor(payload).model_dump_json()
        if call.output_schema == "EvidenceToolPlan":
            return self._scripted_tool_plan(payload).model_dump_json()
        if call.output_schema == "ContextEvidenceOutput":
            return self._scripted_evidence(payload).model_dump_json()
        if call.output_schema == "ImpactOutput":
            return self._scripted_impact(payload).model_dump_json()
        if call.output_schema == "ActionOutput":
            return self._scripted_action(payload).model_dump_json()
        if call.output_schema == "LearningPolicyOutput":
            return self._scripted_learning(payload).model_dump_json()
        raise ValueError(f"Unknown scripted schema: {call.output_schema}")

    def _fallback_response(self, call: AgentCall) -> str:
        payload = call.input_payload
        events = self._events(payload)
        if call.agent_name == SignalSupervisorAgent.name:
            return SignalSupervisorAgent().fallback(
                events,
                dict(payload.get("project_profile", {})),
            ).model_dump_json()
        if call.output_schema == "EvidenceToolPlan":
            return ContextEvidenceAgent().fallback_plan(events).model_dump_json()
        if call.output_schema == "ContextEvidenceOutput":
            plan = EvidenceToolPlan.model_validate(payload.get("tool_plan", {}))
            observations = [
                ToolObservation.model_validate(item)
                for item in payload.get("tool_observations", [])
            ]
            return ContextEvidenceAgent().fallback(
                events,
                plan=plan,
                observations=observations,
            ).model_dump_json()
        if call.agent_name == ImpactAnalystAgent.name:
            return ImpactAnalystAgent().fallback(
                events,
                dict(payload.get("project_profile", {})),
                {},
            ).model_dump_json()
        if call.agent_name == ActionPlannerAgent.name:
            impact = ImpactOutput.model_validate(payload["impact"])
            return ActionPlannerAgent().fallback(events, impact).model_dump_json()
        if call.agent_name == LearningPolicyAgent.name:
            return LearningPolicyAgent().fallback(payload).model_dump_json()
        raise ValueError(f"Unknown fallback Agent: {call.agent_name}")

    def _scripted_supervisor(self, payload: dict[str, Any]) -> SupervisorOutput:
        noise_by_id = {
            str(item["event_id"]): item
            for item in payload.get("noise_assessments", [])
            if isinstance(item, dict)
        }
        cluster_by_event = {
            str(event_id): str(cluster["cluster_id"])
            for cluster in payload.get("clusters", [])
            if isinstance(cluster, dict)
            for event_id in cluster.get("related_event_ids", [])
        }
        routes: list[SupervisorRoute] = []
        for event in self._events(payload):
            noise = noise_by_id.get(event.event_id, {})
            text = f"{event.source_name} {event.title} {event.content}".lower()
            if noise.get("is_noise_candidate"):
                category = SignalCategory.NOISE
            elif event.source_type == "github_release":
                category = SignalCategory.DEPENDENCY_UPDATE
            elif event.source_type == "github_issue":
                category = (
                    SignalCategory.POLICY_SIGNAL
                    if any(term in text for term in ("policy", "permission", "restrict"))
                    else SignalCategory.TEAM_UPDATE
                )
            elif event.source_type == "rss":
                category = SignalCategory.EXPERT_OPINION
            elif any(term in text for term in ("policy", "license", "compliance")):
                category = SignalCategory.POLICY_SIGNAL
            else:
                category = SignalCategory.MARKET_SIGNAL
            if category is SignalCategory.NOISE:
                required: list[RequiredAgent] = []
                analyze = False
            elif category is SignalCategory.EXPERT_OPINION:
                required = ["context_evidence", "impact", "learning_observation"]
                analyze = True
            else:
                required = [
                    "context_evidence",
                    "impact",
                    "action",
                    "learning_observation",
                ]
                analyze = True
            routes.append(
                SupervisorRoute(
                    event_id=event.event_id,
                    category=category,
                    analyze=analyze,
                    routing_reason=(
                        (
                            "Scripted LLM explicit override of a downweight-only noise "
                            "hint because project relevance still requires analysis."
                            if analyze and noise.get("noise_reason")
                            else "Scripted LLM route based on source, content, and noise hints."
                        )
                    ),
                    required_agents=required,
                    skip_reason="Noise route skips deep analysis." if not analyze else None,
                    noise_reason=(
                        str(noise.get("noise_reason")) if noise.get("noise_reason") else None
                    ),
                    related_cluster_id=cluster_by_event.get(event.event_id),
                )
            )
        return SupervisorOutput(
            routes=routes,
            batch_summary="Scripted mock routed the complete event batch.",
        )

    def _scripted_tool_plan(self, payload: dict[str, Any]) -> EvidenceToolPlan:
        events = self._events(payload)
        requests: list[ToolRequest] = []
        if events:
            requests.append(
                ToolRequest(
                    tool_name="signal_memory",
                    arguments={"action": "load_project_profile"},
                    reason="Confirm stable project context before evidence synthesis.",
                )
            )
        by_type: dict[str, list[SignalEvent]] = {}
        for event in events:
            by_type.setdefault(event.source_type, []).append(event)
        for source_type, grouped in by_type.items():
            event_ids = [event.event_id for event in grouped]
            if source_type in {"github_release", "github_issue"}:
                action = (
                    "fetch_repo_issues"
                    if source_type == "github_issue"
                    else "fetch_repo_releases"
                )
                requests.append(
                    ToolRequest(
                        tool_name="github_signal",
                        arguments={
                            "mock_tool_eval": True,
                            "action": action,
                            "repo": self._mock_repo(grouped[0]),
                            "source_type": source_type,
                            "event_ids": event_ids,
                        },
                        reason=(
                            "Read a fixture-safe mocked GitHub signal observation "
                            f"for {source_type} evidence."
                        ),
                    )
                )
            elif source_type == "rss":
                requests.append(
                    ToolRequest(
                        tool_name="rss_signal",
                        arguments={
                            "mock_tool_eval": True,
                            "action": "fetch_feed",
                            "url": grouped[0].url or "https://mock.signalharness.local/rss",
                            "feed_name": grouped[0].source_name,
                            "source_type": source_type,
                            "event_ids": event_ids,
                        },
                        reason="Read a fixture-safe mocked RSS commentary observation.",
                    )
                )
            elif source_type == "web_change":
                requests.append(
                    ToolRequest(
                        tool_name="web_change",
                        arguments={
                            "mock_tool_eval": True,
                            "action": "load_fixture",
                            "fixture": "examples/signal_harness/sample_events.json",
                            "source_type": source_type,
                            "event_ids": event_ids,
                        },
                        reason="Read a fixture-safe mocked web-change observation.",
                    )
                )
        return EvidenceToolPlan(
            source_types_observed=list(
                dict.fromkeys(event.source_type for event in events)
            ),
            tool_requests=requests,
            planning_summary=(
                "Request local memory plus fixture-safe mocked source observations "
                "derived from the observed event source types."
            ),
        )

    @staticmethod
    def _mock_repo(event: SignalEvent) -> str:
        if "/" in event.source_name:
            return event.source_name
        raw_repo = event.raw_payload.get("repo")
        if isinstance(raw_repo, str) and "/" in raw_repo:
            return raw_repo
        return "mock/signal-source"

    def _scripted_evidence(self, payload: dict[str, Any]) -> ContextEvidenceOutput:
        plan = EvidenceToolPlan.model_validate(payload.get("tool_plan", {}))
        observations = [
            ToolObservation.model_validate(item)
            for item in payload.get("tool_observations", [])
        ]
        requested = [request.tool_name for request in plan.tool_requests]
        executed = [
            observation.tool_name
            for observation in observations
            if observation.status == "success"
        ]
        errors = [
            f"{observation.tool_name}: {observation.error or observation.output_summary}"
            for observation in observations
            if observation.status != "success"
        ]
        results: list[ContextEvidenceItem] = []
        for event in self._events(payload):
            official = bool(event.raw_payload.get("official"))
            if official or event.source_type.startswith("github_"):
                quality, confidence = SourceQuality.OFFICIAL, 0.9
            elif event.source_type == "rss":
                quality, confidence = SourceQuality.SECONDARY, 0.68
            elif event.source_type == "web_change":
                quality, confidence = SourceQuality.COMMUNITY, 0.48
            else:
                quality, confidence = SourceQuality.UNVERIFIED, 0.35
            if not event.url:
                confidence -= 0.18
            if errors:
                confidence = min(confidence, 0.55)
            results.append(
                ContextEvidenceItem(
                    event_id=event.event_id,
                    evidence_urls=[event.url] if event.url else [],
                    context_summary=(
                        "Scripted evidence synthesis preserved the supplied source "
                        "and incorporated controlled tool observations."
                    ),
                    confidence=max(0.1, confidence),
                    source_quality=quality,
                    unsupported_claims=[] if event.url else ["Primary URL is missing."],
                    uncertainty="; ".join(errors),
                    source_types_observed=[event.source_type],
                    tools_requested=requested,
                    tools_executed=executed,
                    tool_errors=errors,
                )
            )
        return ContextEvidenceOutput(results=results)

    def _scripted_impact(self, payload: dict[str, Any]) -> ImpactOutput:
        clusters = [
            SignalCluster.model_validate(item)
            for item in payload.get("related_clusters", [])
        ]
        cluster_by_event = {
            event_id: cluster
            for cluster in clusters
            for event_id in cluster.related_event_ids
        }
        results: list[ImpactItem] = []
        for event in self._events(payload):
            text = f"{event.title} {event.content}".lower()
            semantic = 88.0 if "checkpoint" in text else 72.0 if "tool" in text else 55.0
            cluster = cluster_by_event.get(event.event_id)
            conflicts = (
                ["Secondary source expresses uncertainty about the primary claim."]
                if any(term in text for term in ("unreliable", "disputed", "uncertain"))
                else []
            )
            results.append(
                ImpactItem(
                    event_id=event.event_id,
                    affected_modules=["project-wide"] if semantic >= 70 else [],
                    semantic_relevance=semantic,
                    risk_level="high" if semantic >= 75 else "medium",
                    impact_reason="Scripted semantic assessment from evidence and cluster context.",
                    related_event_ids=[
                        item
                        for item in (cluster.related_event_ids if cluster else [])
                        if item != event.event_id
                    ],
                    cross_source_confidence=cluster.confidence if cluster else 0.35,
                    conflicting_evidence=conflicts,
                )
            )
        return ImpactOutput(results=results)

    def _scripted_action(self, payload: dict[str, Any]) -> ActionOutput:
        impact = ImpactOutput.model_validate(payload["impact"])
        impact_by_id = {item.event_id: item for item in impact.results}
        results = []
        for event in self._events(payload):
            item = impact_by_id[event.event_id]
            policy_signal = any(
                term in f"{event.title} {event.content}".lower()
                for term in ("policy", "permission", "license", "compliance")
            )
            results.append(
                ActionItem(
                    event_id=event.event_id,
                    action_items=["Review evidence and assign a human owner."],
                    critic_notes="Scripted critic limits the plan to reversible review work.",
                    approval_required=policy_signal or item.risk_level in {"high", "critical"},
                    requested_actions=[],
                )
            )
        return ActionOutput(results=results)

    def _scripted_learning(self, payload: dict[str, Any]) -> LearningPolicyOutput:
        active = deepcopy(
            payload.get("policy_memory", {}).get("active_policy", {})
        )
        proposal = PolicyUpdateProposal(
            proposal_id=f"mock-proposal-{uuid4().hex[:10]}",
            reason="Scripted mock reviewed memory and preserved the active policy.",
            old_policy=active,
            new_policy=deepcopy(active),
            expected_effect="No automatic mutation; retain a reviewable baseline.",
            requires_approval=True,
        )
        return LearningPolicyOutput(
            policy_update_proposal=proposal,
            skill_update_proposal=(
                "# Scripted skill proposal\n\n"
                "Review evidence uncertainty before escalating a signal.\n"
            ),
            watchlist_update_proposal={
                "requires_approval": True,
                "suggested_changes": [],
                "reason": "No automatic watchlist change.",
            },
            learning_summary="Scripted mock completed a safe learning observation.",
            memory_sections_read=[
                "ProjectMemory",
                "SignalMemory",
                "FeedbackMemory",
                "PolicyMemory",
            ],
        )

    @staticmethod
    def _events(payload: dict[str, Any]) -> list[SignalEvent]:
        return [
            SignalEvent.model_validate(item)
            for item in payload.get("events", [])
        ]

    async def close(self) -> None:
        return None
