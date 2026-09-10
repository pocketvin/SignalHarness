"""ContextEvidenceAgent: context enrichment and evidence verification."""

from __future__ import annotations

from typing import Any

from signal_harness.agent_integration.prompts import PROMPT_VERSION, build_agent_call
from signal_harness.agent_integration.schemas import (
    ContextEvidenceItem,
    ContextEvidenceOutput,
    EvidenceToolPlan,
    SupervisorOutput,
    ToolObservation,
    ToolRequest,
)
from signal_harness.agents.evidence import EvidenceAgent
from signal_harness.providers.adapter import AgentCall
from signal_harness.signal.schemas import SignalCluster, SignalEvent


class ContextEvidenceAgent:
    """LLM Agent that verifies evidence without deciding project impact."""

    name = "ContextEvidenceAgent"
    prompt_version = PROMPT_VERSION
    output_model = ContextEvidenceOutput
    plan_model = EvidenceToolPlan
    available_tools = (
        "github_signal",
        "rss_signal",
        "web_change",
        "signal_memory",
        "signal_score",
    )
    tool_request_contract = {
        "github_signal": {
            "required": ["action"],
            "common_valid_actions": [
                "fetch_repo_releases",
                "fetch_repo_issues",
                "fetch_repo_commits",
                "fetch_repo_merged_pulls",
            ],
            "required_when_applicable": ["repo"],
            "minimal_examples": [
                {
                    "tool_name": "github_signal",
                    "arguments": {
                        "action": "fetch_repo_releases",
                        "repo": "owner/name",
                    },
                    "reason": "Read recent official releases for an observed repository signal.",
                },
                {
                    "tool_name": "github_signal",
                    "arguments": {
                        "action": "fetch_repo_issues",
                        "repo": "owner/name",
                    },
                    "reason": "Read official issues for an observed repository signal.",
                },
            ],
        },
        "rss_signal": {
            "required": ["action"],
            "common_valid_actions": ["fetch_feed"],
            "required_when_applicable": ["url"],
            "minimal_examples": [
                {
                    "tool_name": "rss_signal",
                    "arguments": {
                        "action": "fetch_feed",
                        "url": "https://example.com/feed.xml",
                    },
                    "reason": "Read the observed feed for source corroboration.",
                }
            ],
        },
        "web_change": {
            "required": ["action"],
            "common_valid_actions": ["load_fixture", "fetch_snapshot"],
            "required_when_applicable": ["fixture for load_fixture; url for fetch_snapshot"],
            "minimal_examples": [
                {
                    "tool_name": "web_change",
                    "arguments": {
                        "action": "load_fixture",
                        "fixture": "examples/signal_harness/sample_events.json",
                    },
                    "reason": "Read a local fixture-backed web-change source.",
                },
                {
                    "tool_name": "web_change",
                    "arguments": {
                        "action": "fetch_snapshot",
                        "url": "https://example.com/changelog",
                        "source_name": "Official changelog",
                    },
                    "reason": "Read a configured public page snapshot for corroboration.",
                },
            ],
        },
        "signal_memory": {
            "required": ["action"],
            "valid_action_rule": "action must start with load_",
            "minimal_examples": [
                {
                    "tool_name": "signal_memory",
                    "arguments": {"action": "load_project_profile"},
                    "reason": "Read stable project context.",
                },
                {
                    "tool_name": "signal_memory",
                    "arguments": {"action": "load_signal_policy"},
                    "reason": "Read scoring and permission policy.",
                },
                {
                    "tool_name": "signal_memory",
                    "arguments": {"action": "load_watchlist"},
                    "reason": "Read configured watched sources.",
                },
            ],
        },
    }
    tool_planning_rules = [
        "Do not omit required arguments.",
        "If you are unsure of the required arguments, do not request the tool.",
        "Use existing event context instead of emitting invalid tool calls.",
        "Do not request write or mutation tools.",
        "Python runtime executes or blocks tools; do not pretend execution happened.",
    ]

    def build_tool_plan_call(
        self,
        events: list[SignalEvent],
        routes: SupervisorOutput,
        *,
        project_profile: dict[str, Any],
        policy: dict[str, Any],
        clusters: list[SignalCluster],
        memory: dict[str, Any],
        volatile_metadata: dict[str, Any] | None = None,
    ) -> AgentCall:
        payload = {
            "events": [event.model_dump(mode="json") for event in events],
            "routes": routes.model_dump(mode="json"),
            "related_clusters": [cluster.model_dump(mode="json") for cluster in clusters],
            "source_diversity": sorted({event.source_type for event in events}),
            "source_tool_hints": _source_tool_hints(events),
            "available_tools": list(self.available_tools),
            "tool_request_contract": self.tool_request_contract,
            "tool_planning_rules": self.tool_planning_rules,
            "phase": "tool_planning",
        }
        return build_agent_call(
            agent_name=self.name,
            output_model=self.plan_model,
            dynamic_payload=payload,
            input_count=len(events),
            project_context={
                "project_profile": project_profile,
                "policy": policy,
            },
            memory=memory,
            volatile_metadata=volatile_metadata,
            tool_allowlist=list(self.available_tools),
        )

    def build_final_call(
        self,
        events: list[SignalEvent],
        routes: SupervisorOutput,
        plan: EvidenceToolPlan,
        observations: list[ToolObservation],
        *,
        project_profile: dict[str, Any],
        policy: dict[str, Any],
        clusters: list[SignalCluster],
        memory: dict[str, Any],
        volatile_metadata: dict[str, Any] | None = None,
    ) -> AgentCall:
        payload = {
            "events": [event.model_dump(mode="json") for event in events],
            "routes": routes.model_dump(mode="json"),
            "related_clusters": [cluster.model_dump(mode="json") for cluster in clusters],
            "source_diversity": sorted({event.source_type for event in events}),
            "tool_plan": plan.model_dump(mode="json"),
            "tool_observations": [item.model_dump(mode="json") for item in observations],
            "phase": "final_evidence",
        }
        return build_agent_call(
            agent_name=self.name,
            output_model=self.output_model,
            dynamic_payload=payload,
            input_count=len(events) + len(observations),
            project_context={
                "project_profile": project_profile,
                "policy": policy,
            },
            memory=memory,
            volatile_metadata=volatile_metadata,
            tool_allowlist=list(self.available_tools),
        )

    def fallback_plan(self, events: list[SignalEvent]) -> EvidenceToolPlan:
        requests = [ToolRequest.model_validate(item) for item in _source_tool_hints(events)]
        return EvidenceToolPlan(
            source_types_observed=list(dict.fromkeys(event.source_type for event in events)),
            tool_requests=list({request_key(item): item for item in requests}.values()),
            planning_summary=(
                "Deterministic source-aware fallback requested safe primary-source tools."
            ),
        )

    def fallback(
        self,
        events: list[SignalEvent],
        *,
        plan: EvidenceToolPlan | None = None,
        observations: list[ToolObservation] | None = None,
    ) -> ContextEvidenceOutput:
        evidence_agent = EvidenceAgent()
        active_plan = plan or self.fallback_plan(events)
        active_observations = observations or []
        requested = [item.tool_name for item in active_plan.tool_requests]
        executed = [item.tool_name for item in active_observations if item.status == "success"]
        errors = [
            f"{item.tool_name}: {item.error or item.output_summary}"
            for item in active_observations
            if item.status != "success"
        ]
        uncertainty = "Evidence confidence reduced due to tool errors." if errors else ""
        process_note = "No additional external lookup was performed by the fallback."
        return ContextEvidenceOutput(
            results=[
                ContextEvidenceItem(
                    event_id=event.event_id,
                    evidence_urls=result.evidence_urls,
                    context_summary=(
                        f"Deterministic fallback: {result.reason} {process_note}"
                    ),
                    confidence=min(result.confidence, 0.55) if errors else result.confidence,
                    source_quality=result.source_quality,
                    unsupported_claims=[],
                    uncertainty=uncertainty,
                    source_types_observed=[event.source_type],
                    tools_requested=requested,
                    tools_executed=executed,
                    tool_errors=(
                        ["Evidence confidence reduced due to tool errors."] if errors else []
                    ),
                )
                for event in events
                for result in [evidence_agent.run(event)]
            ]
        )


def _source_tool_hints(events: list[SignalEvent]) -> list[dict[str, Any]]:
    requests = [hint for event in events for hint in [_source_tool_hint(event)] if hint is not None]
    return [
        request.model_dump(mode="json")
        for request in {request_key(item): item for item in requests}.values()
    ]


def request_key(request: ToolRequest) -> tuple[str, tuple[tuple[str, str], ...]]:
    """Stable key for deduping tool requests without losing explicit arguments."""

    return (
        request.tool_name,
        tuple(sorted((str(key), str(value)) for key, value in request.arguments.items())),
    )


def _source_tool_hint(event: SignalEvent) -> ToolRequest | None:
    """Return the safest primary-source read tool for one normalized event."""

    if event.source_type == "github_release" and "/" in event.source_name:
        return ToolRequest(
            tool_name="github_signal",
            arguments={
                "action": "fetch_repo_releases",
                "repo": event.source_name,
            },
            reason="Verify the observed GitHub release from the same repository.",
        )
    if event.source_type == "github_issue" and "/" in event.source_name:
        return ToolRequest(
            tool_name="github_signal",
            arguments={
                "action": "fetch_repo_issues",
                "repo": event.source_name,
            },
            reason="Verify the observed GitHub issue from the same repository.",
        )
    if event.source_type == "github_commit" and "/" in event.source_name:
        return ToolRequest(
            tool_name="github_signal",
            arguments={
                "action": "fetch_repo_commits",
                "repo": event.source_name,
            },
            reason="Verify the observed GitHub commit from the same repository.",
        )
    if event.source_type == "github_pull_request" and "/" in event.source_name:
        return ToolRequest(
            tool_name="github_signal",
            arguments={
                "action": "fetch_repo_merged_pulls",
                "repo": event.source_name,
            },
            reason="Verify the observed merged pull request from the same repository.",
        )
    if event.source_type == "rss":
        feed_url = str(
            event.raw_payload.get("feed_url") or event.raw_payload.get("source_feed_url") or ""
        ).strip()
        if feed_url.startswith(("http://", "https://")):
            return ToolRequest(
                tool_name="rss_signal",
                arguments={"action": "fetch_feed", "url": feed_url},
                reason="Verify the observed RSS item by reading the configured feed URL.",
            )
    if event.source_type == "web_change":
        fixture = str(event.raw_payload.get("fixture") or "").strip()
        if fixture:
            return ToolRequest(
                tool_name="web_change",
                arguments={"action": "load_fixture", "fixture": fixture},
                reason="Verify the observed web-change source from its local fixture.",
            )
    return None
