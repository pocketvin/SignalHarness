"""LearningPolicyAgent: reflection over memory and review-only proposals."""

from __future__ import annotations

from typing import Any

from signal_harness.agent_integration.prompts import PROMPT_VERSION, build_agent_call
from signal_harness.agent_integration.schemas import LearningPolicyOutput
from signal_harness.providers.adapter import AgentCall
from signal_harness.signal.feedback import generate_policy_proposal
from signal_harness.signal.schemas import FeedbackRecord


class LearningPolicyAgent:
    """LLM Agent that uses memory but cannot mutate source configuration."""

    name = "LearningPolicyAgent"
    prompt_version = PROMPT_VERSION
    output_model = LearningPolicyOutput

    def build_call(self, memories: dict[str, Any]) -> AgentCall:
        payload = {
            **memories,
            "constraints": {
                "requires_approval": True,
                "must_not_modify": [
                    "signal_policy.yaml",
                    "watchlist.yaml",
                    "skill files",
                ],
            },
        }
        feedback = memories.get("feedback_memory", [])
        return build_agent_call(
            agent_name=self.name,
            output_model=self.output_model,
            dynamic_payload=payload,
            input_count=4 + (len(feedback) if isinstance(feedback, list) else 0),
            project_context=memories.get("project_memory", {}),
            memory=memories,
        )

    def fallback(self, memories: dict[str, Any]) -> LearningPolicyOutput:
        feedback = [
            FeedbackRecord.model_validate(item)
            for item in memories.get("feedback_memory", [])
        ]
        policy = dict(memories.get("policy_memory", {}).get("active_policy", {}))
        proposal = generate_policy_proposal(feedback, policy)
        watchlist = dict(memories.get("project_memory", {}).get("watchlist", {}))
        return LearningPolicyOutput(
            policy_update_proposal=proposal,
            skill_update_proposal=(
                "# Signal analysis skill proposal\n\n"
                "- Review repeated feedback terms before changing triage heuristics.\n"
                "- Require primary-source evidence for high-risk recommendations.\n"
                "\nApproval is required before editing any skill file.\n"
            ),
            watchlist_update_proposal={
                "requires_approval": True,
                "reason": "No automatic watchlist mutation; review memory-derived suggestions.",
                "current_watchlist": watchlist,
                "suggested_changes": [],
            },
            learning_summary=(
                "Deterministic fallback generated review-only proposals from memory."
            ),
            memory_sections_read=[
                "ProjectMemory",
                "SignalMemory",
                "FeedbackMemory",
                "PolicyMemory",
            ],
        )
