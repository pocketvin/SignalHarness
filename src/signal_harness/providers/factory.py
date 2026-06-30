"""Provider selection for SignalHarness agent mode."""

from __future__ import annotations

import os
from pathlib import Path

from signal_harness.agent_integration.mode import RunMode
from signal_harness.providers.adapter import AgentProvider
from signal_harness.providers.mock_provider import MockProvider
from signal_harness.providers.openai_compatible_provider import OpenAICompatibleProvider


def provider_from_env(
    mode: RunMode,
    *,
    config_dir: str | Path | None = None,
) -> AgentProvider:
    """Create the provider for a run mode without affecting demo/mock isolation."""

    if mode is RunMode.MOCK_AGENT:
        return MockProvider()
    if mode is not RunMode.AGENT:
        raise ValueError("demo mode does not use an LLM provider")
    provider_name = os.environ.get("LLM_PROVIDER", "openai_compatible").strip().lower()
    if provider_name in {"openai_compatible", "openai-compatible", "openai"}:
        return OpenAICompatibleProvider.from_env(config_dir=config_dir)
    raise ValueError(
        "Unsupported LLM_PROVIDER. Use openai_compatible."
    )
