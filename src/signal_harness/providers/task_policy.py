"""Server-owned model selection by responsibility, not a frontend mode switch."""

from __future__ import annotations

import json
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Literal

import yaml

from signal_harness.providers.adapter import AgentProvider
from signal_harness.providers.model_profile import load_model_profile
from signal_harness.providers.catalog import provider_catalog, provider_from_selection

TaskRole = Literal["shallow", "synthesis", "deep_dive"]


@dataclass(frozen=True)
class TaskPolicy:
    config_dir: Path
    version: str = "environment-model-policy-v1"
    batch_size: int = 12
    batch_input_bytes: int = 42000
    shallow_concurrency: int = 3
    global_input_bytes: int = 450000
    tiny_fast_path_max_changes: int = 4
    tiny_fast_path_max_input_bytes: int = 24000
    direction_fact_chars: int = 88
    max_provider_attempts: int = 2
    retry_split_min_batch: int = 4
    roles: dict[str, Any] | None = None
    models: dict[str, str] | None = None

    @classmethod
    def load(cls, config_dir: Path) -> TaskPolicy:
        path = config_dir / "intelligence_policy.yaml"
        raw = yaml.safe_load(path.read_text()) if path.exists() else {}
        raw = raw if isinstance(raw, dict) else {}
        return cls(
            config_dir=config_dir,
            version=str(raw.get("version") or "environment-model-policy-v1"),
            batch_size=max(1, min(24, int(raw.get("batch_size", 12)))),
            batch_input_bytes=max(2000, min(100000, int(raw.get("batch_input_bytes", 42000)))),
            shallow_concurrency=max(1, min(8, int(raw.get("shallow_concurrency", 3)))),
            global_input_bytes=max(10000, min(1000000, int(raw.get("global_input_bytes", 450000)))),
            tiny_fast_path_max_changes=max(
                1, min(12, int(raw.get("tiny_fast_path_max_changes", 4)))
            ),
            tiny_fast_path_max_input_bytes=max(
                2000, min(100000, int(raw.get("tiny_fast_path_max_input_bytes", 24000)))
            ),
            max_provider_attempts=max(1, min(2, int(raw.get("max_provider_attempts", 2)))),
            retry_split_min_batch=max(1, min(8, int(raw.get("retry_split_min_batch", 4)))),
            models=raw.get("models", {}),
            roles={name: raw.get(name, {}) for name in ("shallow", "synthesis", "deep_dive")},
        )

    def providers(self, role: TaskRole) -> list[str]:
        preferred = (
            ["qwen", "kimi", "deepseek"] if role == "shallow" else ["kimi", "deepseek", "qwen"]
        )
        names = (self.roles or {}).get(role, {}).get("providers", preferred)
        ready = {option.provider_id for option in provider_catalog(self.config_dir) if option.ready}
        return list(dict.fromkeys(str(name) for name in names if name in ready))[
            : self.max_provider_attempts
        ]

    def timeout(self, role: TaskRole) -> int:
        default = 100 if role == "shallow" else 180
        return max(
            10, min(240, int((self.roles or {}).get(role, {}).get("timeout_seconds", default)))
        )

    def fingerprint(self, role: TaskRole) -> str:
        models = {
            item.provider_id: (self.models or {}).get(item.provider_id, item.model)
            for item in provider_catalog(self.config_dir)
        }
        return json.dumps(
            {
                "version": self.version,
                "role": role,
                "providers": [(name, models.get(name)) for name in self.providers(role)],
                "policy": self.roles,
            },
            sort_keys=True,
        )

    def create_provider(self, name: str, role: TaskRole) -> AgentProvider:
        # Endpoint/credentials retain their dedicated provider namespace. This explicit
        # server task policy may pin a model instead of a legacy picker/environment override.
        provider = provider_from_selection(name, config_dir=self.config_dir)
        explicit_model = (self.models or {}).get(name)
        if explicit_model:
            profile = load_model_profile(
                provider.model_profile, config_dir=self.config_dir, apply_env_model_override=False
            )
            provider.profile = profile.with_model_override(explicit_model)
            provider.model = explicit_model
        maximum = int((self.roles or {}).get(role, {}).get("max_output_tokens", 8192))
        provider.profile = replace(provider.profile, max_output_tokens=min(maximum, 16000))
        if name == "qwen":
            # Non-streaming structured Qwen extraction: provider-specific, not a UI setting.
            provider.request_options = {"enable_thinking": False}
        elif name == "deepseek":
            provider.request_options = {
                "thinking": {"type": "disabled" if role == "shallow" else "enabled"}
            }
            if role != "shallow":
                provider.request_options["reasoning_effort"] = "high"
        # Kimi K3's model default is retained; never send unverified thinking parameters.
        provider.set_timeout(self.timeout(role))
        return provider
