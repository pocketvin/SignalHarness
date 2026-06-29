"""Optional provider adapter for OpenHarness-compatible model clients."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

from signal_harness.providers.adapter import AgentCall


@dataclass(frozen=True)
class ProviderMessage:
    """Minimal message shape for injected provider clients in tests/integrations."""

    role: str
    text: str


@dataclass(frozen=True)
class ProviderRequest:
    """Minimal request shape accepted by OpenAI-compatible injected clients."""

    model: str
    messages: list[ProviderMessage]
    system_prompt: str
    max_tokens: int
    tools: list[dict[str, Any]]


class OpenHarnessProvider:
    """Collect structured text from an optional OpenHarness streaming client."""

    name = "openharness-openai-compatible"

    def __init__(
        self,
        *,
        api_key: str,
        model: str = "gpt-4o-mini",
        base_url: str | None = None,
        client: Any | None = None,
    ) -> None:
        self.model = model
        self._uses_openharness_request = client is None
        self._client = client or self._openharness_client(
            api_key,
            base_url=base_url,
        )

    @classmethod
    def from_env(cls) -> "OpenHarnessProvider":
        api_key = os.environ.get("LLM_API_KEY", "").strip()
        if not api_key:
            raise ValueError(
                "agent mode requires LLM_API_KEY. "
                "Use --mode demo or --mode mock-agent for offline execution."
            )
        return cls(
            api_key=api_key,
            model=os.environ.get("LLM_MODEL", "gpt-4o-mini"),
            base_url=os.environ.get("LLM_BASE_URL") or None,
        )

    async def complete(self, call: AgentCall) -> str:
        request = self._request(call)
        final_text = ""
        async for event in self._client.stream_message(request):
            text = self._event_text(event)
            if text is not None:
                final_text = text
        if not final_text.strip():
            raise RuntimeError(f"{call.agent_name} returned an empty response")
        return final_text

    async def close(self) -> None:
        close = getattr(self._client, "close", None)
        if close is not None:
            await close()

    @staticmethod
    def _openharness_client(api_key: str, *, base_url: str | None) -> Any:
        try:
            from openharness.api.openai_client import OpenAICompatibleClient
        except ModuleNotFoundError as exc:
            raise RuntimeError(
                "agent mode requires the optional OpenHarness provider runtime. "
                "Use --mode demo or --mode mock-agent for offline execution, or "
                "install the OpenHarness integration before running --mode agent."
            ) from exc
        return OpenAICompatibleClient(api_key, base_url=base_url)

    def _request(self, call: AgentCall) -> Any:
        if not self._uses_openharness_request:
            return ProviderRequest(
                model=self.model,
                messages=[ProviderMessage(role="user", text=call.user_prompt)],
                system_prompt=call.system_prompt,
                max_tokens=4096,
                tools=call.tools,
            )
        try:
            from openharness.api.client import ApiMessageRequest
            from openharness.engine.messages import ConversationMessage
        except ModuleNotFoundError as exc:
            raise RuntimeError(
                "agent mode requires the optional OpenHarness provider runtime."
            ) from exc
        return ApiMessageRequest(
            model=self.model,
            messages=[ConversationMessage.from_user_text(call.user_prompt)],
            system_prompt=call.system_prompt,
            max_tokens=4096,
            tools=call.tools,
        )

    @staticmethod
    def _event_text(event: Any) -> str | None:
        message = getattr(event, "message", None)
        if message is None:
            return None
        text = getattr(message, "text", None)
        if isinstance(text, str):
            return text
        content = getattr(message, "content", None)
        if isinstance(content, list):
            parts = [
                item
                for block in content
                for item in (getattr(block, "text", None),)
                if isinstance(item, str)
            ]
            return "".join(parts) if parts else None
        return None
