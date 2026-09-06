"""Non-secret provider catalog for selectable real-model runs."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from signal_harness.providers.model_profile import load_model_profile
from signal_harness.providers.openai_compatible_provider import OpenAICompatibleProvider

_PROVIDER_SPECS = (
    ("openai", "OpenAI", "OPENAI", "openai_gpt4o_mini"),
    ("qwen", "Qwen", "QWEN", "qwen"),
    ("kimi", "Kimi", "KIMI", "kimi"),
    ("deepseek", "DeepSeek", "DEEPSEEK", "deepseek"),
)


@dataclass(frozen=True)
class ProviderOption:
    """Safe provider metadata suitable for UI exposure."""

    provider_id: str
    label: str
    env_prefix: str
    profile_name: str
    model: str | None
    ready: bool
    reason: str | None = None

    def public_payload(self) -> dict[str, Any]:
        return {
            "id": self.provider_id,
            "label": self.label,
            "profile": self.profile_name,
            "model": self.model,
            "ready": self.ready,
            "reason": self.reason,
        }


def provider_catalog(config_dir: str | Path) -> list[ProviderOption]:
    """Return configured provider options without exposing keys or base URLs."""

    root = Path(config_dir).expanduser().resolve()
    options: list[ProviderOption] = []
    for provider_id, label, prefix, default_profile in _PROVIDER_SPECS:
        profile_name = os.environ.get(f"{prefix}_MODEL_PROFILE", default_profile).strip() or default_profile
        key_present = bool(os.environ.get(f"{prefix}_API_KEY", "").strip())
        base_present = bool(os.environ.get(f"{prefix}_BASE_URL", "").strip())
        try:
            profile = load_model_profile(profile_name, config_dir=root)
            model = os.environ.get(f"{prefix}_MODEL", "").strip() or profile.model
        except (OSError, ValueError):
            options.append(
                ProviderOption(provider_id, label, prefix, profile_name, None, False, "invalid_model_profile")
            )
            continue
        reason = None
        if not key_present:
            reason = "missing_api_key"
        elif not base_present:
            reason = "missing_base_url"
        options.append(
            ProviderOption(provider_id, label, prefix, profile_name, model, reason is None, reason)
        )
    return options


def provider_option(provider_id: str, config_dir: str | Path) -> ProviderOption:
    normalized = provider_id.strip().lower()
    for option in provider_catalog(config_dir):
        if option.provider_id == normalized:
            return option
    raise ValueError(f"Unknown provider selection: {provider_id}")


def provider_from_selection(
    provider_id: str,
    *,
    config_dir: str | Path,
) -> OpenAICompatibleProvider:
    """Create one provider from its dedicated environment namespace."""

    option = provider_option(provider_id, config_dir)
    if not option.ready:
        raise ValueError(f"Provider {provider_id} is not ready: {option.reason}")
    prefix = option.env_prefix
    api_key = os.environ[f"{prefix}_API_KEY"].strip()
    base_url = os.environ[f"{prefix}_BASE_URL"].strip()
    profile = load_model_profile(option.profile_name, config_dir=config_dir).with_model_override(option.model)
    return OpenAICompatibleProvider(
        api_key=api_key,
        base_url=base_url,
        profile=profile,
        model_profile=option.profile_name,
        provider_label=option.provider_id,
        request_sleep_seconds=float(os.environ.get("LLM_REQUEST_SLEEP_SECONDS", "0") or 0),
    )


def default_provider_id(config_dir: str | Path) -> str | None:
    """Pick the configured profile selected by LLM_* first, then the first ready provider."""

    selected_profile = os.environ.get("LLM_MODEL_PROFILE", "").strip()
    options = provider_catalog(config_dir)
    if selected_profile:
        selected_stem = Path(selected_profile).stem
        for option in options:
            if option.ready and option.profile_name == selected_stem:
                return option.provider_id
    for option in options:
        if option.ready:
            return option.provider_id
    return None
