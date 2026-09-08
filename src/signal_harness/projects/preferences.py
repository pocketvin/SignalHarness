"""Explicit project preference contracts and deterministic profile merging."""

from __future__ import annotations

import re
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class Importance(str, Enum):
    CRITICAL = "critical"
    IMPORTANT = "important"
    NORMAL = "normal"
    LOW = "low"
    IGNORE = "ignore"


class PreferenceScope(str, Enum):
    DEPENDENCY = "dependency"
    PROVIDER = "provider"
    RUNTIME = "runtime"
    PROTOCOL = "protocol"
    MODULE = "module"
    ECOSYSTEM = "ecosystem"
    SOURCE = "source"
    CATEGORY = "category"
    TOPIC = "topic"

class PreferenceInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scope_type: PreferenceScope
    scope_key: str = Field(min_length=1, max_length=160)
    importance: Importance
    source: Literal["ui", "natural_language", "api", "cli"] = "api"
    instruction: str = Field(default="", max_length=1000)
    note: str = Field(default="", max_length=1000)

    @field_validator("scope_key")
    @classmethod
    def _normalize_scope_key(cls, value: str) -> str:
        normalized = " ".join(value.split()).strip()
        if not normalized:
            raise ValueError("scope_key cannot be empty")
        return normalized


_IMPORTANCE_PHRASES: tuple[tuple[Importance, tuple[str, ...]], ...] = (
    (Importance.IGNORE, ("不要关注", "不关注", "忽略", "ignore", "don't focus on", "do not focus on")),
    (Importance.CRITICAL, ("最重要", "非常重要", "关键", "critical", "highest priority")),
    (Importance.LOW, ("较低", "降低关注", "少关注", "低优先级", "low priority", "low")),
    (Importance.NORMAL, ("正常", "一般", "普通", "normal")),
    (Importance.IMPORTANT, ("重要", "关注", "important", "focus on", "add", "include")),
)

def apply_preferences(
    auto_profile: dict[str, Any],
    preferences: list[dict[str, Any]],
) -> dict[str, Any]:
    """Return the effective profile while preserving explicit preference metadata."""

    profile = dict(auto_profile)
    active = [item for item in preferences if bool(item.get("active", True))]
    profile["importance_preferences"] = [
        {
            "preference_id": str(item.get("preference_id") or ""),
            "scope_type": str(item.get("scope_type") or "topic"),
            "scope_key": str(item.get("scope_key") or ""),
            "importance": str(item.get("importance") or Importance.NORMAL.value),
            "source": str(item.get("source") or "api"),
        }
        for item in active
    ]
    return profile


def parse_preference_instruction(
    instruction: str,
    profile: dict[str, Any],
) -> PreferenceInput:
    """Parse common user importance instructions without an LLM or hidden side effects."""

    text = " ".join(instruction.split()).strip()
    if not text:
        raise ValueError("instruction cannot be empty")
    lowered = text.lower()
    importance = _detect_importance(lowered)
    scope_type, scope_key = _detect_scope(text, profile)
    return PreferenceInput(
        scope_type=scope_type,
        scope_key=scope_key,
        importance=importance,
        source="natural_language",
        instruction=text,
    )

def _detect_importance(lowered: str) -> Importance:
    for importance, phrases in _IMPORTANCE_PHRASES:
        if any(phrase in lowered for phrase in phrases):
            return importance
    return Importance.IMPORTANT


def _detect_scope(text: str, profile: dict[str, Any]) -> tuple[PreferenceScope, str]:
    lowered = text.lower()
    groups: tuple[tuple[PreferenceScope, str], ...] = (
        (PreferenceScope.DEPENDENCY, "dependencies"),
        (PreferenceScope.PROVIDER, "providers"),
        (PreferenceScope.RUNTIME, "runtimes"),
        (PreferenceScope.PROTOCOL, "protocols"),
        (PreferenceScope.MODULE, "critical_modules"),
        (PreferenceScope.ECOSYSTEM, "monitored_ecosystem"),
    )
    candidates: list[tuple[int, PreferenceScope, str]] = []
    for scope, key in groups:
        values = profile.get(key, [])
        if not isinstance(values, list):
            continue
        for value in values:
            label = str(value).strip()
            if label and label.lower() in lowered:
                candidates.append((len(label), scope, label))
    if candidates:
        _, scope, label = max(candidates, key=lambda item: item[0])
        return scope, label

    cleaned = lowered
    for _, phrases in _IMPORTANCE_PHRASES:
        for phrase in phrases:
            cleaned = cleaned.replace(phrase, " ")
    cleaned = re.sub(r"\b(?:please|the|a|an|is|are|to|for|ecosystem|topic)\b", " ", cleaned)
    cleaned = re.sub(r"(?:请|把|将|设为|设置为|这个|生态|相关|一下)", " ", cleaned)
    cleaned = re.sub(r"[^\w@./+\-\u4e00-\u9fff]+", " ", cleaned)
    target = " ".join(cleaned.split()).strip(" .,/+-")
    if not target:
        raise ValueError("could not determine preference target")
    return PreferenceScope.TOPIC, target[:160]
