"""ImpactAnalystAgent: project-specific semantic impact analysis."""

from __future__ import annotations

from typing import Any, Literal

from signal_harness.agent_integration.prompts import PROMPT_VERSION, build_agent_call
from signal_harness.agent_integration.schemas import (
    ContextEvidenceOutput,
    ImpactItem,
    ImpactOutput,
    SupervisorOutput,
)
from signal_harness.providers.adapter import AgentCall
from signal_harness.signal.scorer import relevance_score
from signal_harness.signal.schemas import SignalCluster, SignalEvent


class ImpactAnalystAgent:
    """LLM Agent that may emit semantic relevance but never final_score."""

    name = "ImpactAnalystAgent"
    prompt_version = PROMPT_VERSION
    output_model = ImpactOutput

    def build_call(
        self,
        events: list[SignalEvent],
        project_profile: dict[str, Any],
        routes: SupervisorOutput,
        evidence: ContextEvidenceOutput,
        *,
        clusters: list[SignalCluster] | None = None,
        policy: dict[str, Any] | None = None,
        volatile_metadata: dict[str, Any] | None = None,
    ) -> AgentCall:
        payload = {
            "events": [event.model_dump(mode="json") for event in events],
            "project_profile": project_profile,
            "routes": routes.model_dump(mode="json"),
            "evidence": evidence.model_dump(mode="json"),
            "related_clusters": [
                cluster.model_dump(mode="json") for cluster in clusters or []
            ],
            "constraints": [
                "Do not emit final_score.",
                (
                    "repair_requests are suggestions only; Python decides whether "
                    "a bounded repair pass is allowed."
                ),
                (
                    "If repair is needed, ImpactAnalystAgent may only request "
                    "target_agent=context_evidence for the relevant event_ids."
                ),
                (
                    "Never request supervisor, action, learning, recursive, or "
                    "provider-native tool-calling handoff repairs."
                ),
            ],
        }
        return build_agent_call(
            agent_name=self.name,
            output_model=self.output_model,
            dynamic_payload=payload,
            input_count=len(events),
            project_context={
                "project_profile": project_profile,
                "policy": policy or {},
            },
            volatile_metadata=volatile_metadata,
        )

    def fallback(
        self,
        events: list[SignalEvent],
        project_profile: dict[str, Any],
        policy: dict[str, Any],
        clusters: list[SignalCluster] | None = None,
    ) -> ImpactOutput:
        cluster_by_event = {
            event_id: cluster
            for cluster in clusters or []
            for event_id in cluster.related_event_ids
        }
        results: list[ImpactItem] = []
        for event in events:
            semantic = relevance_score(event, project_profile, policy)
            text = f"{event.source_name} {event.title} {event.content}".lower()
            modules = [
                str(module)
                for module in project_profile.get("critical_modules", [])
                if str(module).lower() in text
            ]
            if not modules and semantic >= 60:
                modules = ["project-wide"]
            risk: Literal["low", "medium", "high", "critical"] = (
                "critical"
                if semantic >= 90
                else "high"
                if semantic >= 75
                else "medium"
                if semantic >= 45
                else "low"
            )
            results.append(
                ImpactItem(
                    event_id=event.event_id,
                    affected_modules=modules,
                    semantic_relevance=semantic,
                    risk_level=risk,
                    impact_reason=(
                        "Deterministic fallback based on project-profile term overlap."
                    ),
                    related_event_ids=[
                        item
                        for item in cluster_by_event.get(
                            event.event_id,
                            SignalCluster(
                                cluster_id="none",
                                topic="none",
                                related_event_ids=[event.event_id],
                                time_window="single-event",
                                confidence=0,
                            ),
                        ).related_event_ids
                        if item != event.event_id
                    ],
                    cross_source_confidence=cluster_by_event.get(
                        event.event_id,
                        SignalCluster(
                            cluster_id="none",
                            topic="none",
                            related_event_ids=[event.event_id],
                            time_window="single-event",
                            confidence=0,
                        ),
                    ).confidence,
                )
            )
        return ImpactOutput(results=results)
