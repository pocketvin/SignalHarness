"""Versioned prompts for the five SignalHarness LLM Agents."""

from __future__ import annotations

import json
from typing import Any

from pydantic import BaseModel

from signal_harness.agent_integration.context_builder import PromptContextBuilder
from signal_harness.providers.adapter import AgentCall

PROMPT_VERSION = "signal-harness-llm-v1"

SYSTEM_PROMPTS = {
    "SignalSupervisorAgent": (
        "You are SignalSupervisorAgent. Classify and route the supplied SignalEvent batch. "
        "Do not perform deep evidence analysis, create actions, update policy, or emit scores."
    ),
    "ContextEvidenceAgent": (
        "You are ContextEvidenceAgent. In planning phase propose only allowlisted read-only "
        "ToolRequest objects. In final phase use ToolObservation objects to verify context and "
        "provenance, prefer primary sources, state uncertainty, and never decide project impact "
        "or create action items. Do not omit required tool arguments. If you are unsure of "
        "the required arguments, do not request the tool; use existing event context instead. "
        "Do not request write or mutation tools. Python will execute or block tools; never "
        "pretend tool execution happened."
    ),
    "ImpactAnalystAgent": (
        "You are ImpactAnalystAgent. Judge affected modules, semantic relevance, and risk from "
        "the project profile and evidence. Never emit final_score; Python owns final scoring. "
        "If evidence is too weak for a high-risk event, you may suggest repair_requests only "
        "for target_agent=context_evidence. Treat repair as a suggestion; Python decides "
        "whether a bounded repair pass runs."
    ),
    "ActionPlannerAgent": (
        "You are ActionPlannerAgent. Propose non-mutating review actions, critique overreach, "
        "and mark risky actions for approval. Never execute actions. If the impact analysis "
        "appears inconsistent with requested actions or approval needs, you may suggest "
        "repair_requests only for target_agent=impact. Treat repair as a suggestion; Python "
        "decides whether a bounded repair pass runs."
    ),
    "LearningPolicyAgent": (
        "You are LearningPolicyAgent. Read project, signal, feedback, and policy memory and "
        "produce review-only policy, skill, and watchlist proposals. Never apply changes."
    ),
}


def render_user_prompt(
    agent_name: str,
    output_schema: dict[str, Any],
    payload: dict[str, Any],
) -> str:
    """Render a JSON-only structured-output request."""

    return (
        f"Return one JSON object matching this schema exactly:\n"
        f"{json.dumps(output_schema, ensure_ascii=False, sort_keys=True)}\n\n"
        f"Input:\n{json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)}\n\n"
        f"Agent: {agent_name}. Do not wrap the JSON in Markdown."
    )


def build_agent_call(
    *,
    agent_name: str,
    output_model: type[BaseModel],
    dynamic_payload: dict[str, Any],
    input_count: int,
    project_context: dict[str, Any] | None = None,
    memory: dict[str, Any] | None = None,
    volatile_metadata: dict[str, Any] | None = None,
    tool_allowlist: list[str] | None = None,
) -> AgentCall:
    """Create one AgentCall from the shared layered context contract."""

    packet = PromptContextBuilder().build(
        agent_name=agent_name,
        role_instructions=SYSTEM_PROMPTS[agent_name],
        output_schema=output_model.model_json_schema(),
        project_context=project_context,
        memory=memory,
        dynamic_payload=dynamic_payload,
        volatile_metadata=volatile_metadata,
        tool_allowlist=tool_allowlist,
    )
    return AgentCall(
        agent_name=agent_name,
        system_prompt=packet.system_prompt(),
        user_prompt=packet.user_prompt(),
        prompt_version=PROMPT_VERSION,
        output_schema=output_model.__name__,
        input_payload=dynamic_payload,
        input_count=input_count,
        prompt_prefix_hash=packet.prompt_prefix_hash,
        static_context_hash=packet.static_context_hash,
        dynamic_context_hash=packet.dynamic_context_hash,
        context_packet_version=packet.version,
        cache_strategy=packet.cache_strategy,
    )
