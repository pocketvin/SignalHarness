"""Explicit SignalHarness execution modes."""

from __future__ import annotations

from enum import Enum


class RunMode(str, Enum):
    """Select deterministic fallback, offline agent validation, or a real LLM."""

    DEMO = "demo"
    MOCK_AGENT = "mock-agent"
    AGENT = "agent"

    @property
    def uses_llm_agent_path(self) -> bool:
        return self in {RunMode.MOCK_AGENT, RunMode.AGENT}
