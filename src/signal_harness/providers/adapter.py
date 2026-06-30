"""Small domain adapter over a single structured-completion provider call."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass(frozen=True)
class AgentCall:
    """One structured Agent invocation passed to a provider."""

    agent_name: str
    system_prompt: str
    user_prompt: str
    prompt_version: str
    output_schema: str
    input_payload: dict[str, Any]
    tools: list[dict[str, Any]] = field(default_factory=list)
    input_count: int = 0
    prompt_prefix_hash: str = ""
    static_context_hash: str = ""
    dynamic_context_hash: str = ""
    context_packet_version: str = ""
    cache_strategy: str = "prefix-metadata-only"


class AgentProvider(Protocol):
    """Provider surface intentionally limited to one structured completion."""

    name: str
    model: str

    async def complete(self, call: AgentCall) -> str:
        """Return the assistant text for one Agent call."""

    async def close(self) -> None:
        """Release provider resources."""
