"""SignalHarness-native OpenAI-compatible chat completions provider."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import httpx

from signal_harness.providers.adapter import AgentCall
from signal_harness.providers.model_profile import ModelProfile, load_model_profile


class OpenAICompatibleProvider:
    """Return assistant text from a /v1/chat/completions compatible API."""

    name = "openai-compatible"

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str,
        profile: ModelProfile,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.model = profile.model
        self.profile = profile
        self.base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._client = client or httpx.AsyncClient(timeout=httpx.Timeout(60.0))
        self._owns_client = client is None

    @classmethod
    def from_env(
        cls,
        *,
        config_dir: str | Path | None = None,
        client: httpx.AsyncClient | None = None,
    ) -> "OpenAICompatibleProvider":
        api_key = os.environ.get("LLM_API_KEY", "").strip()
        if not api_key:
            raise ValueError(
                "agent mode requires LLM_API_KEY. "
                "Use --mode demo or --mode mock-agent for offline execution."
            )
        profile = load_model_profile(config_dir=config_dir)
        return cls(
            api_key=api_key,
            base_url=os.environ.get("LLM_BASE_URL", "https://api.openai.com"),
            profile=profile,
            client=client,
        )

    async def complete(self, call: AgentCall) -> str:
        response = await self._client.post(
            self._chat_completions_url(),
            headers={
                "Authorization": f"Bearer {self._api_key}",
                "Content-Type": "application/json",
            },
            json=self._payload(call),
        )
        response.raise_for_status()
        return _assistant_text(response.json(), agent_name=call.agent_name)

    async def close(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    def _chat_completions_url(self) -> str:
        if self.base_url.endswith("/v1"):
            return f"{self.base_url}/chat/completions"
        return f"{self.base_url}/v1/chat/completions"

    def _payload(self, call: AgentCall) -> dict[str, Any]:
        if self.profile.supports_system_prompt:
            messages = [
                {"role": "system", "content": call.system_prompt},
                {"role": "user", "content": call.user_prompt},
            ]
        else:
            messages = [
                {
                    "role": "user",
                    "content": f"{call.system_prompt}\n\n{call.user_prompt}",
                }
            ]
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "temperature": self.profile.recommended_temperature,
            "max_tokens": self.profile.max_output_tokens,
        }
        if self.profile.supports_json_mode:
            payload["response_format"] = {"type": "json_object"}
        return payload


def _assistant_text(payload: Any, *, agent_name: str) -> str:
    if not isinstance(payload, dict):
        raise RuntimeError(f"{agent_name} provider response must be a JSON object")
    choices = payload.get("choices")
    if not isinstance(choices, list) or not choices:
        raise RuntimeError(f"{agent_name} provider response has no choices")
    first = choices[0]
    if not isinstance(first, dict):
        raise RuntimeError(f"{agent_name} provider choice must be an object")
    message = first.get("message")
    if not isinstance(message, dict):
        raise RuntimeError(f"{agent_name} provider choice has no message")
    content = message.get("content")
    if isinstance(content, str) and content.strip():
        return content
    if isinstance(content, list):
        parts = [
            str(item.get("text"))
            for item in content
            if isinstance(item, dict) and isinstance(item.get("text"), str)
        ]
        text = "".join(parts)
        if text.strip():
            return text
    raise RuntimeError(f"{agent_name} returned an empty response")
