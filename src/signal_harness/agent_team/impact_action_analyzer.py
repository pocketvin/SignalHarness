"""ImpactActionAnalyzerAgent: merged impact reasoning and bounded action proposals."""

from __future__ import annotations

from typing import Any

from signal_harness.agent_integration.prompts import PROMPT_VERSION, build_agent_call
from signal_harness.agent_integration.schemas import (
    ContextEvidenceOutput,
    ImpactActionItem,
    ImpactActionOutput,
    SupervisorOutput,
)
from signal_harness.agent_team.action_planner import ActionPlannerAgent
from signal_harness.agent_team.impact_analyst import ImpactAnalystAgent
from signal_harness.providers.adapter import AgentCall
from signal_harness.signal.schemas import SignalCluster, SignalEvent


class ImpactActionAnalyzerAgent:
    """Experimental merged Agent; Python still owns score, policy, and execution permission."""

    name = "ImpactActionAnalyzerAgent"
    prompt_version = PROMPT_VERSION
    output_model = ImpactActionOutput

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
            "related_clusters": [cluster.model_dump(mode="json") for cluster in clusters or []],
            "high_risk_actions": [
                "modify_signal_policy",
                "add_watchlist_source",
                "remove_watchlist_source",
                "create_github_issue",
                "send_team_notification",
                "modify_project_profile",
            ],
            "constraints": [
                "Do not emit or infer final_score; Python owns final scoring and decisions.",
                "Propose actions only; never execute tools or mutate project state.",
                "Keep requested_actions empty unless a named high-risk action is truly required.",
            ],
        }
        return build_agent_call(
            agent_name=self.name,
            output_model=self.output_model,
            dynamic_payload=payload,
            input_count=len(events),
            project_context={"project_profile": project_profile, "policy": policy or {}},
            volatile_metadata=volatile_metadata,
        )

    def fallback(
        self,
        events: list[SignalEvent],
        project_profile: dict[str, Any],
        policy: dict[str, Any],
        clusters: list[SignalCluster] | None = None,
    ) -> ImpactActionOutput:
        impact = ImpactAnalystAgent().fallback(events, project_profile, policy, clusters)
        action = ActionPlannerAgent().fallback(events, impact)
        impact_by_id = {item.event_id: item for item in impact.results}
        action_by_id = {item.event_id: item for item in action.results}
        return ImpactActionOutput(
            results=[
                ImpactActionItem(
                    event_id=event.event_id,
                    impact=impact_by_id[event.event_id],
                    action=action_by_id[event.event_id],
                )
                for event in events
            ]
        )
