"""Presentation-only sanitizers for user-facing SignalHarness prose."""

from __future__ import annotations

import re
from collections.abc import Iterable

PRESENTATION_VERSION = "presentation-v2"

_APPROVAL_WRAPPER = re.compile(
    r"^Approval required before\s+`([^`]+)`\s*:\s*.*(?:is not enabled|requires approval).*$",
    re.IGNORECASE,
)
_INTERNAL_ACTION_MARKERS = (
    "human approval is required before execution",
    "approval required before",
    " is not enabled",
    "permission_checks",
    "requires_approval",
    "fallback_used",
    "schema_valid",
    "score_breakdown",
    "agent_score_breakdown",
    "routing_reason",
)
_INTERNAL_IDENTIFIER = re.compile(r"^[A-Za-z][A-Za-z0-9_.:/-]*(?:_[A-Za-z0-9_.:/-]+)+$")


def _has_chinese(text: str, *, minimum: int = 2) -> bool:
    return sum("\u4e00" <= char <= "\u9fff" for char in text) >= minimum


def sanitize_user_facing_action(value: object) -> str | None:
    """Return one product-safe action, or None for runtime/debug boilerplate.

    Permission/audit strings remain available in the underlying guarded assessment; this
    function only controls the user-facing projection.
    """

    text = " ".join(str(value or "").split()).strip()
    if not text:
        return None

    wrapped = _APPROVAL_WRAPPER.match(text)
    if wrapped:
        substantive = " ".join(wrapped.group(1).split()).strip()
        # Very short approval labels (for example "代码审查" / "回归验证") are runtime
        # categories rather than useful product recommendations. Keep only a substantive step.
        if _has_chinese(substantive, minimum=6) and not _INTERNAL_IDENTIFIER.fullmatch(substantive):
            return substantive
        return None

    lowered = text.lower()
    if any(marker in lowered for marker in _INTERNAL_ACTION_MARKERS):
        return None
    if _INTERNAL_IDENTIFIER.fullmatch(text):
        return None
    # Chinese-facing action lists may contain technical tokens such as MCP/HTTPX/API, but
    # should not expose a fully English runtime sentence as product copy.
    if not _has_chinese(text) and re.search(r"[A-Za-z]", text):
        return None
    return text


def sanitize_user_facing_actions(values: Iterable[object]) -> list[str]:
    """Filter/dedupe actions while preserving their original user-relevant order."""

    cleaned: list[str] = []
    for value in values:
        action = sanitize_user_facing_action(value)
        if action and action not in cleaned:
            cleaned.append(action)
    return cleaned
