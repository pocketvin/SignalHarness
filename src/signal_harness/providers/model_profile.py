"""Conservative model capability profiles for SignalHarness providers."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

import yaml

SchemaStrategy = Literal["prompt_json_retry"]
ToolStrategy = Literal["controlled_tool_request"]


@dataclass(frozen=True)
class ModelProfile:
    """Document model capabilities without changing SignalHarness guardrails."""

    provider: str
    model: str
    supports_json_schema: bool = False
    supports_json_mode: bool = False
    supports_native_tool_calling: bool = False
    supports_system_prompt: bool = True
    max_input_tokens: int = 8192
    max_output_tokens: int = 4096
    recommended_temperature: float = 0.0
    schema_strategy: SchemaStrategy = "prompt_json_retry"
    tool_strategy: ToolStrategy = "controlled_tool_request"

    @classmethod
    def from_yaml(cls, path: str | Path) -> "ModelProfile":
        payload = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError(f"Model profile must be a YAML mapping: {path}")
        return cls.from_mapping(payload)

    @classmethod
    def from_mapping(cls, payload: dict[str, Any]) -> "ModelProfile":
        profile = cls(
            provider=str(payload.get("provider", "openai_compatible")),
            model=str(payload.get("model", "")),
            supports_json_schema=bool(payload.get("supports_json_schema", False)),
            supports_json_mode=bool(payload.get("supports_json_mode", False)),
            supports_native_tool_calling=bool(
                payload.get("supports_native_tool_calling", False)
            ),
            supports_system_prompt=bool(payload.get("supports_system_prompt", True)),
            max_input_tokens=int(payload.get("max_input_tokens", 8192)),
            max_output_tokens=int(payload.get("max_output_tokens", 4096)),
            recommended_temperature=float(
                payload.get("recommended_temperature", 0.0)
            ),
            schema_strategy=_schema_strategy(payload.get("schema_strategy")),
            tool_strategy=_tool_strategy(payload.get("tool_strategy")),
        )
        if not profile.model:
            raise ValueError("Model profile requires a non-empty model")
        if profile.supports_native_tool_calling:
            raise ValueError(
                "SignalHarness profiles must keep supports_native_tool_calling=false "
                "until provider-native tool calling is explicitly implemented."
            )
        return profile

    def with_model_override(self, model: str | None) -> "ModelProfile":
        if not model:
            return self
        return ModelProfile(
            provider=self.provider,
            model=model,
            supports_json_schema=self.supports_json_schema,
            supports_json_mode=self.supports_json_mode,
            supports_native_tool_calling=self.supports_native_tool_calling,
            supports_system_prompt=self.supports_system_prompt,
            max_input_tokens=self.max_input_tokens,
            max_output_tokens=self.max_output_tokens,
            recommended_temperature=self.recommended_temperature,
            schema_strategy=self.schema_strategy,
            tool_strategy=self.tool_strategy,
        )


def load_model_profile(
    value: str | Path | None = None,
    *,
    config_dir: str | Path | None = None,
) -> ModelProfile:
    """Load a profile by name or path.

    `value` may be a filesystem path or a profile stem such as `kimi`.
    """

    selected = str(
        value
        or os.environ.get("LLM_MODEL_PROFILE")
        or "openai_gpt4o_mini"
    ).strip()
    path = Path(selected).expanduser()
    if not path.suffix:
        profile_root = (
            Path(config_dir).expanduser().resolve()
            if config_dir is not None
            else Path.cwd() / "configs"
        )
        path = profile_root / "model_profiles" / f"{selected}.yaml"
    if not path.exists():
        raise FileNotFoundError(f"Model profile not found: {path}")
    profile = ModelProfile.from_yaml(path)
    return profile.with_model_override(os.environ.get("LLM_MODEL"))


def _schema_strategy(value: object) -> SchemaStrategy:
    if value in (None, "prompt_json_retry"):
        return "prompt_json_retry"
    raise ValueError(f"Unsupported schema_strategy: {value}")


def _tool_strategy(value: object) -> ToolStrategy:
    if value in (None, "controlled_tool_request"):
        return "controlled_tool_request"
    raise ValueError(f"Unsupported tool_strategy: {value}")
