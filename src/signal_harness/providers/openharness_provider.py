"""Real provider adapter that reuses OpenHarness's native model client."""

from __future__ import annotations

import os

from openharness.api.client import (
    ApiMessageCompleteEvent,
    ApiMessageRequest,
    SupportsStreamingMessages,
)
from openharness.api.openai_client import OpenAICompatibleClient
from openharness.engine.messages import ConversationMessage

from signal_harness.providers.adapter import AgentCall


class OpenHarnessProvider:
    """Collect structured text from an existing OpenHarness streaming client."""

    name = "openharness-openai-compatible"

    def __init__(
        self,
        *,
        api_key: str,
        model: str = "gpt-4o-mini",
        base_url: str | None = None,
        client: SupportsStreamingMessages | None = None,
    ) -> None:
        self.model = model
        self._client = client or OpenAICompatibleClient(
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
        request = ApiMessageRequest(
            model=self.model,
            messages=[ConversationMessage.from_user_text(call.user_prompt)],
            system_prompt=call.system_prompt,
            max_tokens=4096,
            tools=call.tools,
        )
        final_text = ""
        async for event in self._client.stream_message(request):
            if isinstance(event, ApiMessageCompleteEvent):
                final_text = event.message.text
        if not final_text.strip():
            raise RuntimeError(f"{call.agent_name} returned an empty response")
        return final_text

    async def close(self) -> None:
        close = getattr(self._client, "close", None)
        if close is not None:
            await close()
