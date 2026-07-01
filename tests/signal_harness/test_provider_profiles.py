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
    assert profile.output_token_parameter == "max_tokens"


def test_kimi_profile_uses_max_completion_tokens(project_root: Path) -> None:
    profile = load_model_profile(
        "kimi",
        config_dir=project_root / "configs",
    )

    assert profile.output_token_parameter == "max_completion_tokens"
    assert profile.supports_json_mode is False
    assert profile.supports_native_tool_calling is False
    assert profile.schema_strategy == "prompt_json_retry"
    assert profile.tool_strategy == "controlled_tool_request"
    assert profile.recommended_temperature == 1.0


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


def test_model_profile_rejects_unknown_output_token_parameter() -> None:
    try:
        ModelProfile.from_mapping(
            {
                "provider": "openai_compatible",
                "model": "unsafe",
                "output_token_parameter": "completion_tokens",
            }
        )
    except ValueError as exc:
        assert "Unsupported output_token_parameter" in str(exc)
    else:
        raise AssertionError("unknown output token parameter should fail")


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
    assert requests[0]["max_tokens"] == 4096
    assert "max_completion_tokens" not in requests[0]
    assert requests[0]["response_format"] == {"type": "json_object"}


def test_kimi_payload_uses_only_max_completion_tokens(project_root: Path) -> None:
    requests: list[dict[str, object]] = []

    def handler(request: httpx.Request) -> httpx.Response:
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

    profile = load_model_profile("kimi", config_dir=project_root / "configs")
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
    assert requests[0]["max_completion_tokens"] == 4096
    assert "max_tokens" not in requests[0]


def test_http_status_error_includes_safe_body_without_sensitive_headers(
    project_root: Path,
) -> None:
    sensitive_header = "Authori" + "zation"
    sensitive_prefix = "Bear" + "er"
    secret_prefix = "s" + "k-"

    def handler(request: httpx.Request) -> httpx.Response:
        del request
        return httpx.Response(
            400,
            text=(
                "bad parameter: use max_completion_tokens; "
                f"{sensitive_header}: {sensitive_prefix} {secret_prefix}abc123"
            ),
        )

    profile = load_model_profile("kimi", config_dir=project_root / "configs")
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = OpenAICompatibleProvider(
        api_key="test-key",
        base_url="https://example.test",
        profile=profile,
        client=client,
    )
    try:
        try:
            asyncio.run(provider.complete(_call()))
        except RuntimeError as exc:
            message = str(exc)
        else:
            raise AssertionError("HTTP 400 should be surfaced as a RuntimeError")
    finally:
        asyncio.run(client.aclose())

    assert "status_code=400" in message
    assert "bad parameter: use max_completion_tokens" in message
    assert sensitive_header not in message
    assert sensitive_prefix not in message
    assert secret_prefix not in message


def test_provider_factory_defaults_to_openai_compatible(
    project_root: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("LLM_API_KEY", "test-key")
    monkeypatch.delenv("LLM_PROVIDER", raising=False)

    provider = provider_from_env(RunMode.AGENT, config_dir=project_root / "configs")

    assert isinstance(provider, OpenAICompatibleProvider)
    asyncio.run(provider.close())


def test_provider_factory_rejects_unsupported_provider(monkeypatch) -> None:
    monkeypatch.setenv("LLM_API_KEY", "test-key")
    monkeypatch.setenv("LLM_PROVIDER", "unsupported-provider")

    try:
        provider_from_env(RunMode.AGENT)
    except ValueError as exc:
        assert "Unsupported LLM_PROVIDER" in str(exc)
        assert "openai_compatible" in str(exc)
    else:
        raise AssertionError("unsupported provider should fail clearly")


def test_mock_provider_needs_no_llm_api_key(monkeypatch) -> None:
    monkeypatch.delenv("LLM_API_KEY", raising=False)

    assert isinstance(provider_from_env(RunMode.MOCK_AGENT), MockProvider)
