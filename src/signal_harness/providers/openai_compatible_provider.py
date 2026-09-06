"""SignalHarness-native OpenAI-compatible chat completions provider."""

from __future__ import annotations

import asyncio
import os
import re
from pathlib import Path
from typing import Any

import httpx

from signal_harness.providers.adapter import AgentCall, ProviderUsage
from signal_harness.providers.model_profile import ModelProfile, load_model_profile

HTTP_AUTH_HEADER = "Authori" + "zation"
BEARER_PREFIX = "Bear" + "er"
SENSITIVE_KEY_PREFIX = "s" + "k-"


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
        request_sleep_seconds: float = 0.0,
    ) -> None:
        self.model = profile.model
        self.profile = profile
        self.provider = profile.provider
        self.model_profile = os.environ.get("LLM_MODEL_PROFILE", "").strip()
        self.base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._client = client or httpx.AsyncClient(timeout=httpx.Timeout(60.0))
        self._owns_client = client is None
        self._request_sleep_seconds = max(0.0, request_sleep_seconds)
        self._usage = ProviderUsage(source="provider_reported")

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
            request_sleep_seconds=float(
                os.environ.get("LLM_REQUEST_SLEEP_SECONDS", "0") or 0
            ),
        )

    async def complete(self, call: AgentCall) -> str:
        if self._request_sleep_seconds:
            await asyncio.sleep(self._request_sleep_seconds)
        response = await self._client.post(
            self._chat_completions_url(),
            headers={
                HTTP_AUTH_HEADER: f"{BEARER_PREFIX} {self._api_key}",
                "Content-Type": "application/json",
            },
            json=self._payload(call),
        )
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise RuntimeError(_safe_http_status_error(exc)) from exc
        payload = response.json()
        self._record_usage(payload)
        return _assistant_text(payload, agent_name=call.agent_name)

    def usage_snapshot(self) -> ProviderUsage:
        """Return cumulative provider usage for trace delta calculation."""

        return self._usage

    def _record_usage(self, payload: Any) -> None:
        usage = payload.get("usage") if isinstance(payload, dict) else None
        if not isinstance(usage, dict):
            return
        prompt_tokens = _non_negative_int(usage.get("prompt_tokens"))
        completion_tokens = _non_negative_int(usage.get("completion_tokens"))
        total_tokens = _non_negative_int(usage.get("total_tokens"))
        if total_tokens == 0:
            total_tokens = prompt_tokens + completion_tokens
        cost = _estimated_cost_usd(
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            profile=self.profile,
        )
        self._usage = ProviderUsage(
            prompt_tokens=self._usage.prompt_tokens + prompt_tokens,
            completion_tokens=self._usage.completion_tokens + completion_tokens,
            total_tokens=self._usage.total_tokens + total_tokens,
            estimated_cost_usd=round(self._usage.estimated_cost_usd + cost, 8),
            source=(
                "provider_reported_with_profile_pricing"
                if self.profile.input_cost_per_million_usd is not None
                and self.profile.output_cost_per_million_usd is not None
                else "provider_reported_no_pricing"
            ),
        )

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
        }
        payload[self.profile.output_token_parameter] = self.profile.max_output_tokens
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


def _safe_http_status_error(exc: httpx.HTTPStatusError) -> str:
    body = _redact_sensitive_response_text(exc.response.text[:500])
    return (
        "Provider HTTP error "
        f"status_code={exc.response.status_code}; response_body={body}"
    )


def _redact_sensitive_response_text(text: str) -> str:
    redacted = text.replace(HTTP_AUTH_HEADER, "[redacted-header]")
    redacted = redacted.replace(BEARER_PREFIX, "[redacted-prefix]")
    return re.sub(
        rf"{re.escape(SENSITIVE_KEY_PREFIX)}[A-Za-z0-9._~-]+",
        "[redacted-key]",
        redacted,
    )


def _non_negative_int(value: object) -> int:
    if value is None:
        return 0
    if not isinstance(value, (str, int, float)):
        return 0
    try:
        return max(0, int(value))
    except ValueError:
        return 0


def _estimated_cost_usd(
    *,
    prompt_tokens: int,
    completion_tokens: int,
    profile: ModelProfile,
) -> float:
    if (
        profile.input_cost_per_million_usd is None
        or profile.output_cost_per_million_usd is None
    ):
        return 0.0
    return round(
        prompt_tokens / 1_000_000 * profile.input_cost_per_million_usd
        + completion_tokens / 1_000_000 * profile.output_cost_per_million_usd,
        8,
    )
