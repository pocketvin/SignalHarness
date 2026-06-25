"""Layered prompt context with stable-prefix metadata."""

from __future__ import annotations

import hashlib
import json
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

CONTEXT_PACKET_VERSION = "signal-context-v1"


def _stable_json(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )


def _hash(value: object) -> str:
    return hashlib.sha256(_stable_json(value).encode("utf-8")).hexdigest()


class ContextPacket(BaseModel):
    """Five prompt layers plus hashes used for trace-level cache observability."""

    model_config = ConfigDict(extra="forbid")

    version: str = CONTEXT_PACKET_VERSION
    static_instructions: dict[str, Any]
    stable_project_context: dict[str, Any] = Field(default_factory=dict)
    semi_stable_memory: dict[str, Any] = Field(default_factory=dict)
    dynamic_task_payload: dict[str, Any] = Field(default_factory=dict)
    volatile_metadata: dict[str, Any] = Field(default_factory=dict)
    prompt_prefix_hash: str
    static_context_hash: str
    dynamic_context_hash: str
    cache_strategy: str = "prefix-metadata-only"

    def system_prompt(self) -> str:
        """Render stable and semi-stable layers first."""

        prefix = {
            "context_packet_version": self.version,
            "static_agent_instructions": self.static_instructions,
            "stable_project_context": self.stable_project_context,
            "semi_stable_memory_summary": self.semi_stable_memory,
        }
        return _stable_json(prefix)

    def user_prompt(self) -> str:
        """Render dynamic task data before volatile run metadata."""

        return _stable_json(
            {
                "dynamic_task_payload": self.dynamic_task_payload,
                "volatile_metadata": self.volatile_metadata,
            }
        )


class PromptContextBuilder:
    """Build compact context without copying complete histories into prompts."""

    def build(
        self,
        *,
        agent_name: str,
        role_instructions: str,
        output_schema: dict[str, Any],
        project_context: dict[str, Any] | None = None,
        memory: dict[str, Any] | None = None,
        dynamic_payload: dict[str, Any] | None = None,
        volatile_metadata: dict[str, Any] | None = None,
        tool_allowlist: list[str] | None = None,
    ) -> ContextPacket:
        static = {
            "agent_name": agent_name,
            "role": role_instructions,
            "output_schema": output_schema,
            "output_rule": "Return one JSON object only; do not use Markdown fences.",
            "permission_rule": "Requested tools and actions remain subject to Python guards.",
            "score_rule": "LLM output cannot set or override authoritative final_score.",
        }
        stable = self._project_summary(project_context or {}, tool_allowlist or [])
        semi_stable = self._memory_summary(memory or {})
        dynamic = dynamic_payload or {}
        volatile = volatile_metadata or {}
        static_hash = _hash({"static": static, "stable": stable})
        prefix_hash = _hash(
            {
                "static": static,
                "stable": stable,
                "semi_stable": semi_stable,
            }
        )
        return ContextPacket(
            static_instructions=static,
            stable_project_context=stable,
            semi_stable_memory=semi_stable,
            dynamic_task_payload=dynamic,
            volatile_metadata=volatile,
            prompt_prefix_hash=prefix_hash,
            static_context_hash=static_hash,
            dynamic_context_hash=_hash(dynamic),
        )

    @staticmethod
    def _project_summary(
        context: dict[str, Any],
        tool_allowlist: list[str],
    ) -> dict[str, Any]:
        profile = context.get("project_profile", context)
        policy = context.get("policy", {})
        return {
            "project": {
                "project_name": profile.get("project_name"),
                "goal": profile.get("goal"),
                "tech_stack": profile.get("tech_stack", []),
                "critical_modules": profile.get("critical_modules", []),
                "dependencies": profile.get("dependencies", []),
                "competitors": profile.get("competitors", []),
                "focus_keywords": profile.get("focus_keywords", []),
                "ignore_keywords": profile.get("ignore_keywords", []),
            },
            "policy": {
                "version": policy.get("version"),
                "thresholds": policy.get("thresholds", {}),
                "source_weights": policy.get("source_weights", {}),
                "category_weights": policy.get("category_weights", {}),
                "ignore_patterns": policy.get("ignore_patterns", []),
            },
            "tool_allowlist": sorted(tool_allowlist),
            "stable_skill_summaries": [
                "signal triage",
                "evidence verification",
                "project impact analysis",
                "signal policy calibration",
            ],
        }

    @staticmethod
    def _memory_summary(memory: dict[str, Any]) -> dict[str, Any]:
        project = memory.get("project_memory", {})
        policy = memory.get("policy_memory", {})
        signal = memory.get("signal_memory", {})
        feedback = memory.get("feedback_memory", [])
        previous = signal.get("previous_assessments", [])
        return {
            "project_memory_keys": sorted(project.keys()) if isinstance(project, dict) else [],
            "policy_version": (
                policy.get("active_policy", {}).get("version")
                if isinstance(policy, dict)
                else None
            ),
            "seen_signal_count": len(signal.get("seen_signals", []))
            if isinstance(signal, dict)
            else 0,
            "recent_assessment_ids": [
                str(item.get("event_id"))
                for item in previous[-10:]
                if isinstance(item, dict)
            ],
            "feedback_count": len(feedback) if isinstance(feedback, list) else 0,
            "recent_feedback_notes": [
                str(item.get("note", ""))[:160]
                for item in feedback[-5:]
                if isinstance(item, dict) and item.get("note")
            ],
        }
