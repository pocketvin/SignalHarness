from __future__ import annotations

import asyncio
import json
from pathlib import Path

import httpx

from signal_harness.agent_integration.mode import RunMode
from signal_harness.providers.adapter import AgentCall
from signal_harness.providers.factory import provider_from_env
from signal_harness.providers.mock_provider import MockProvider
from signal_harness.providers.model_profile import ModelProfile, load_model_profile
from signal_harness.providers.openai_compatible_provider import OpenAICompatibleProvider
from signal_harness.providers.openharness_provider import OpenHarnessProvider


class _FakeProvider:
    name = "fake-openharness"
    model = "fake"

    async def complete(self, call: AgentCall) -> str:
        return "{}"

    async def close(self) -> None:
        return None


def _call() -> AgentCall:
    return AgentCall(
        agent_name="SignalSupervisorAgent",
        system_prompt="Return JSON.",
        user_prompt="Hello",
        prompt_version="v1",
        output_schema="SupervisorOutput",
        input_payload={},
        input_count=1,
    )


def test_model_profile_loads_from_yaml(project_root: Path) -> None:
    profile = load_model_profile(
        "openai_gpt4o_mini",
        config_dir=project_root / "configs",
    )

    assert profile.provider == "openai_compatible"
    assert profile.model == "gpt-4o-mini"
    assert profile.schema_strategy == "prompt_json_retry"
    assert profile.tool_strategy == "controlled_tool_request"
    assert profile.supports_native_tool_calling is False


def test_model_profile_rejects_native_tool_calling_claim() -> None:
    try:
        ModelProfile.from_mapping(
            {
                "provider": "openai_compatible",
                "model": "unsafe",
                "supports_native_tool_calling": True,
            }
        )
    except ValueError as exc:
        assert "supports_native_tool_calling=false" in str(exc)
    else:
        raise AssertionError("native tool-calling profiles must be rejected")


def test_openai_compatible_provider_uses_chat_completions_mock_transport(
    project_root: Path,
) -> None:
    requests: list[dict[str, object]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/chat/completions"
        body = json.loads(request.content.decode("utf-8"))
        requests.append(body)
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": '{"routes":[],"batch_summary":"ok"}',
                        }
                    }
                ]
            },
        )

    profile = load_model_profile(
        "openai_gpt4o_mini",
        config_dir=project_root / "configs",
    )
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = OpenAICompatibleProvider(
        api_key="test-key",
        base_url="https://example.test",
        profile=profile,
        client=client,
    )
    try:
        response = asyncio.run(provider.complete(_call()))
    finally:
        asyncio.run(client.aclose())

    assert response == '{"routes":[],"batch_summary":"ok"}'
    assert requests
    assert requests[0]["model"] == "gpt-4o-mini"
    assert requests[0]["response_format"] == {"type": "json_object"}


def test_provider_factory_defaults_to_openai_compatible(
    project_root: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("LLM_API_KEY", "test-key")
    monkeypatch.delenv("LLM_PROVIDER", raising=False)

    provider = provider_from_env(RunMode.AGENT, config_dir=project_root / "configs")

    assert isinstance(provider, OpenAICompatibleProvider)
    asyncio.run(provider.close())


def test_provider_factory_openharness_remains_optional(monkeypatch) -> None:
    fake = _FakeProvider()
    monkeypatch.setenv("LLM_API_KEY", "test-key")
    monkeypatch.setenv("LLM_PROVIDER", "openharness")
    monkeypatch.setattr(
        OpenHarnessProvider,
        "from_env",
        classmethod(lambda cls: fake),
    )

    assert provider_from_env(RunMode.AGENT) is fake


def test_mock_provider_needs_no_llm_api_key(monkeypatch) -> None:
    monkeypatch.delenv("LLM_API_KEY", raising=False)

    assert isinstance(provider_from_env(RunMode.MOCK_AGENT), MockProvider)
