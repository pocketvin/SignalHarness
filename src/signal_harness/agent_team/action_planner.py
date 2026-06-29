"""ActionPlannerAgent: bounded action proposals and critic review."""

from __future__ import annotations

from typing import Any

from signal_harness.agent_integration.prompts import PROMPT_VERSION, build_agent_call
from signal_harness.agent_integration.schemas import ActionItem, ActionOutput, ImpactOutput
from signal_harness.providers.adapter import AgentCall
from signal_harness.signal.schemas import SignalEvent


class ActionPlannerAgent:
    """LLM Agent that proposes actions but never executes them."""

    name = "ActionPlannerAgent"
    prompt_version = PROMPT_VERSION
    output_model = ActionOutput

    def build_call(
        self,
        events: list[SignalEvent],
        impact: ImpactOutput,
        *,
        project_profile: dict[str, Any] | None = None,
        policy: dict[str, Any] | None = None,
        volatile_metadata: dict[str, Any] | None = None,
    ) -> AgentCall:
        payload = {
            "events": [event.model_dump(mode="json") for event in events],
            "impact": impact.model_dump(mode="json"),
            "high_risk_actions": [
                "modify_signal_policy",
                "add_watchlist_source",
                "remove_watchlist_source",
                "create_github_issue",
                "send_team_notification",
                "modify_project_profile",
            ],
            "repair_constraints": [
                (
                    "repair_requests are suggestions only; Python decides whether "
                    "a bounded repair pass is allowed."
                ),
                (
                    "If repair is needed, ActionPlannerAgent may only request "
                    "target_agent=impact for the relevant event_ids."
                ),
                (
                    "Never request supervisor, context_evidence, learning, recursive, "
                    "or provider-native tool-calling handoff repairs."
                ),
            ],
        }
        return build_agent_call(
            agent_name=self.name,
            output_model=self.output_model,
            dynamic_payload=payload,
            input_count=len(events),
            project_context={
                "project_profile": project_profile or {},
                "policy": policy or {},
            },
            volatile_metadata=volatile_metadata,
        )

    def fallback(
        self,
        events: list[SignalEvent],
        impact: ImpactOutput,
    ) -> ActionOutput:
        impact_by_id = {item.event_id: item for item in impact.results}
        results: list[ActionItem] = []
        for event in events:
            item = impact_by_id[event.event_id]
            target = ", ".join(item.affected_modules) or "the project"
            actions = [f"Review the primary source and validate impact on {target}."]
            if event.url:
                actions.append(f"Preserve the evidence link for review: {event.url}")
            results.append(
                ActionItem(
                    event_id=event.event_id,
                    action_items=actions,
                    critic_notes=(
                        "Deterministic fallback limits output to review-only actions."
                    ),
                    approval_required=item.risk_level in {"high", "critical"},
                    requested_actions=[],
                )
            )
        return ActionOutput(results=results)
