"""SignalSupervisorAgent: batch classification and routing."""

from __future__ import annotations

from typing import Any

from signal_harness.agent_integration.prompts import PROMPT_VERSION, build_agent_call
from signal_harness.agent_integration.schemas import (
    RequiredAgent,
    SupervisorOutput,
    SupervisorRoute,
)
from signal_harness.agents.classifier import ClassifierAgent
from signal_harness.providers.adapter import AgentCall
from signal_harness.signal.schemas import NoiseAssessment, SignalCategory, SignalCluster, SignalEvent


class SignalSupervisorAgent:
    """LLM Agent responsible only for initial classification and routing."""

    name = "SignalSupervisorAgent"
    prompt_version = PROMPT_VERSION
    output_model = SupervisorOutput

    def build_call(
        self,
        events: list[SignalEvent],
        project_profile: dict[str, Any],
        *,
        policy: dict[str, Any] | None = None,
        noise_assessments: list[NoiseAssessment] | None = None,
        clusters: list[SignalCluster] | None = None,
        volatile_metadata: dict[str, Any] | None = None,
    ) -> AgentCall:
        payload = {
            "events": [event.model_dump(mode="json") for event in events],
            "noise_assessments": [
                item.model_dump(mode="json") for item in noise_assessments or []
            ],
            "clusters": [item.model_dump(mode="json") for item in clusters or []],
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
        noise_assessments: list[NoiseAssessment] | None = None,
        clusters: list[SignalCluster] | None = None,
    ) -> SupervisorOutput:
        classifier = ClassifierAgent()
        noise_by_id = {
            item.event_id: item for item in noise_assessments or []
        }
        cluster_by_event = {
            event_id: cluster.cluster_id
            for cluster in clusters or []
            for event_id in cluster.related_event_ids
        }
        routes = []
        for event in events:
            result = classifier.run(event, project_profile)
            noise = noise_by_id.get(event.event_id)
            category = (
                SignalCategory.NOISE
                if noise is not None and noise.is_noise_candidate
                else result.category
            )
            if category is SignalCategory.NOISE:
                required_agents: list[RequiredAgent] = []
                analyze = False
                skip_reason = "Pre-LLM noise rules recommend ignore."
            elif category is SignalCategory.EXPERT_OPINION:
                required_agents = [
                    "context_evidence",
                    "impact",
                    "learning_observation",
                ]
                analyze = True
                skip_reason = None
            else:
                required_agents = [
                    "context_evidence",
                    "impact",
                    "action",
                    "learning_observation",
                ]
                analyze = True
                skip_reason = None
            routes.append(
                SupervisorRoute(
                    event_id=event.event_id,
                    category=category,
                    analyze=analyze,
                    routing_reason=(
                        f"Deterministic fallback: {result.reason}"
                        + (
                            (
                                f" Explicit override of the noise/downweight hint: "
                                f"{noise.noise_reason}"
                                if analyze
                                else f" Noise hint: {noise.noise_reason}"
                            )
                            if noise is not None and noise.noise_reason
                            else ""
                        )
                    ),
                    required_agents=required_agents,
                    skip_reason=skip_reason,
                    noise_reason=noise.noise_reason if noise else None,
                    related_cluster_id=cluster_by_event.get(event.event_id),
                )
            )
        return SupervisorOutput(
            routes=routes,
            batch_summary="Deterministic supervisor fallback was used.",
        )
