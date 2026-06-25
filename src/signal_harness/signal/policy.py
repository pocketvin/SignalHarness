"""Signal policy loading, validation, decision thresholds, and safe updates."""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any

import yaml

from openharness.utils.fs import atomic_write_text
from signal_harness.signal.schemas import SignalDecision

REQUIRED_SCORE_WEIGHTS = {
    "source_weight",
    "keyword_match_score",
    "project_relevance_score",
    "novelty_score",
    "urgency_score",
    "feedback_adjustment",
}


def load_yaml_mapping(path: str | Path) -> dict[str, Any]:
    """Load a YAML mapping and reject empty or non-object documents."""

    resolved = Path(path).expanduser().resolve()
    payload = yaml.safe_load(resolved.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"Expected a YAML mapping in {resolved}")
    return payload


def load_signal_policy(path: str | Path) -> dict[str, Any]:
    """Load and validate a SignalHarness scoring policy."""

    policy = load_yaml_mapping(path)
    weights = policy.get("score_weights")
    if not isinstance(weights, dict) or set(weights) != REQUIRED_SCORE_WEIGHTS:
        raise ValueError("score_weights must define every SignalHarness score component")
    total = sum(float(value) for value in weights.values())
    if abs(total - 1.0) > 1e-9:
        raise ValueError(f"score_weights must sum to 1.0, got {total}")
    thresholds = policy.get("thresholds")
    if not isinstance(thresholds, dict):
        raise ValueError("thresholds must be a mapping")
    ordered = [
        float(thresholds.get("save", 45)),
        float(thresholds.get("alert", 75)),
        float(thresholds.get("action_required", 85)),
    ]
    if ordered != sorted(ordered):
        raise ValueError("thresholds must satisfy save <= alert <= action_required")
    return policy


def decision_for_score(score: float, policy: dict[str, Any]) -> SignalDecision:
    """Map a deterministic score to a business decision."""

    thresholds = policy["thresholds"]
    if score >= float(thresholds["action_required"]):
        return SignalDecision.ACTION_REQUIRED
    if score >= float(thresholds["alert"]):
        return SignalDecision.ALERT
    if score >= float(thresholds["save"]):
        return SignalDecision.SAVE
    return SignalDecision.IGNORE


def render_policy_diff(old_policy: dict[str, Any], new_policy: dict[str, Any]) -> str:
    """Render a compact human-readable policy diff."""

    lines: list[str] = []
    keys = sorted(set(old_policy) | set(new_policy))
    for key in keys:
        old_value = old_policy.get(key)
        new_value = new_policy.get(key)
        if old_value != new_value:
            lines.append(f"- {key}: {old_value!r}")
            lines.append(f"+ {key}: {new_value!r}")
    return "\n".join(lines) or "(no policy changes)"


def apply_policy_proposal(
    policy_path: str | Path,
    new_policy: dict[str, Any],
    *,
    approved: bool,
) -> Path:
    """Apply an explicitly approved proposal using an atomic write."""

    if not approved:
        raise PermissionError("Policy changes require explicit user approval")
    target = Path(policy_path).expanduser().resolve()
    validated = deepcopy(new_policy)
    weights = validated.get("score_weights")
    if not isinstance(weights, dict):
        raise ValueError("Proposed policy is missing score_weights")
    total = sum(float(value) for value in weights.values())
    if abs(total - 1.0) > 1e-9:
        raise ValueError("Proposed score_weights must sum to 1.0")
    atomic_write_text(target, yaml.safe_dump(validated, sort_keys=False, allow_unicode=True))
    return target
