from __future__ import annotations

import asyncio
import json
from pathlib import Path

import httpx

from signal_harness.agent_integration.mode import RunMode
from signal_harness.providers.adapter import AgentCall
from signal_harness.providers.catalog import (
    default_provider_id,
    provider_catalog,
    provider_from_selection,
)
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
    assert profile.recommended_temperature == 0.0


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
    assert requests[0]["max_completion_tokens"] == 2048
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


def test_openai_provider_records_reported_usage_and_profile_cost(
    project_root: Path,
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        del request
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": '{"routes":[],"batch_summary":"ok"}'}}],
                "usage": {
                    "prompt_tokens": 1000,
                    "completion_tokens": 500,
                    "total_tokens": 1500,
                },
            },
        )

    profile = load_model_profile("openai_gpt4o_mini", config_dir=project_root / "configs")
    assert profile.input_cost_per_million_usd == 0.15
    assert profile.output_cost_per_million_usd == 0.60
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = OpenAICompatibleProvider(
        api_key="test-key",
        base_url="https://example.test",
        profile=profile,
        client=client,
    )
    try:
        asyncio.run(provider.complete(_call()))
        usage = provider.usage_snapshot()
    finally:
        asyncio.run(client.aclose())

    assert usage.prompt_tokens == 1000
    assert usage.completion_tokens == 500
    assert usage.total_tokens == 1500
    assert usage.estimated_cost_usd == 0.00045
    assert usage.source == "provider_reported_with_profile_pricing"


def test_provider_catalog_exposes_multiple_non_secret_options(
    project_root: Path, monkeypatch
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "secret-openai")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://api.openai.example/v1")
    monkeypatch.setenv("OPENAI_MODEL", "gpt-4o-mini")
    monkeypatch.setenv("QWEN_API_KEY", "secret-qwen")
    monkeypatch.setenv("QWEN_BASE_URL", "https://qwen.example/v1")
    monkeypatch.setenv("QWEN_MODEL", "qwen-plus")
    monkeypatch.setenv("LLM_MODEL_PROFILE", "qwen")

    options = provider_catalog(project_root / "configs")
    public = [item.public_payload() for item in options]
    by_id = {item["id"]: item for item in public}

    assert by_id["openai"]["ready"] is True
    assert by_id["qwen"]["ready"] is True
    assert by_id["qwen"]["model"] == "qwen-plus"
    assert default_provider_id(project_root / "configs") == "qwen"
    serialized = json.dumps(public)
    assert "secret-openai" not in serialized
    assert "secret-qwen" not in serialized
    assert "base_url" not in serialized


def test_provider_from_selection_uses_selected_namespace(
    project_root: Path, monkeypatch
) -> None:
    monkeypatch.setenv("KIMI_API_KEY", "test-key")
    monkeypatch.setenv("KIMI_BASE_URL", "https://kimi.example/v1")
    monkeypatch.setenv("KIMI_MODEL", "kimi-latest")
    monkeypatch.setenv("KIMI_MODEL_PROFILE", "kimi")

    provider = provider_from_selection("kimi", config_dir=project_root / "configs")
    try:
        assert provider.provider == "kimi"
        assert provider.model == "kimi-latest"
        assert provider.model_profile == "kimi"
        assert provider.base_url == "https://kimi.example/v1"
    finally:
        asyncio.run(provider.close())
