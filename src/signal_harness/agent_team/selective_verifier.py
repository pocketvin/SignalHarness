"""SelectiveVerifierAgent: bounded second-look verification for uncertain/high-risk cases."""

from __future__ import annotations

from typing import Any

from signal_harness.agent_integration.prompts import PROMPT_VERSION, build_agent_call
from signal_harness.agent_integration.schemas import (
    ActionOutput,
    ContextEvidenceOutput,
    ImpactOutput,
    VerificationItem,
    VerificationOutput,
)
from signal_harness.providers.adapter import AgentCall
from signal_harness.signal.schemas import SignalEvent


class SelectiveVerifierAgent:
    """Experimental verifier that can only recommend conservative caps."""

    name = "SelectiveVerifierAgent"
    prompt_version = PROMPT_VERSION
    output_model = VerificationOutput

    def build_call(
        self,
        events: list[SignalEvent],
        evidence: ContextEvidenceOutput,
        impact: ImpactOutput,
        action: ActionOutput,
        *,
        project_profile: dict[str, Any],
        policy: dict[str, Any],
        volatile_metadata: dict[str, Any] | None = None,
    ) -> AgentCall:
        payload = {
            "events": [event.model_dump(mode="json") for event in events],
            "evidence": evidence.model_dump(mode="json"),
            "impact": impact.model_dump(mode="json"),
            "action": action.model_dump(mode="json"),
            "constraints": [
                "Verify support and overstatement only; never raise relevance, risk, or confidence.",
                "Never execute actions or request tools.",
                "Use confidence_cap only to lower confidence when evidence is insufficient.",
            ],
        }
        return build_agent_call(
            agent_name=self.name,
            output_model=self.output_model,
            dynamic_payload=payload,
            input_count=len(events),
            project_context={"project_profile": project_profile, "policy": policy},
            volatile_metadata=volatile_metadata,
        )

    def fallback(self, events: list[SignalEvent]) -> VerificationOutput:
        return VerificationOutput(
            results=[
                VerificationItem(
                    event_id=event.event_id,
                    supported=True,
                    notes="Deterministic fallback made no verifier adjustment.",
                )
                for event in events
            ]
        )
