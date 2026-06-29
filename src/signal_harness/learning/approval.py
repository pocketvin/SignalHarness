"""Explicit apply gate for staged learning proposals."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from signal_harness.learning.staging import (
    StagedLearningProposal,
    load_learning_staging,
    save_learning_staging,
)
from signal_harness.runtime.permissions import SignalPermissionGuard
from signal_harness.signal.policy import apply_policy_proposal, load_signal_policy
from signal_harness.utils.fs import atomic_write_text


def apply_staged_learning(
    *,
    state_dir: str | Path,
    config_dir: str | Path,
    proposal_id: str,
    yes: bool,
) -> Path:
    """Apply one low-risk staged policy proposal after explicit confirmation."""

    state = Path(state_dir).expanduser().resolve()
    config = Path(config_dir).expanduser().resolve()
    proposals = load_learning_staging(state)
    staged = next((item for item in proposals if item.proposal_id == proposal_id), None)
    if staged is None:
        raise ValueError(f"Unknown staged proposal_id: {proposal_id}")
    _require_apply_allowed(staged, yes=yes)
    policy_path = config / "signal_policy.yaml"
    active_policy = load_signal_policy(policy_path)
    SignalPermissionGuard(active_policy).require("modify_signal_policy", confirmed=yes)
    new_policy = staged.learning["policy_update_proposal"]["new_policy"]
    if not isinstance(new_policy, dict):
        raise ValueError("Staged proposal is missing a new_policy mapping")
    applied_path = apply_policy_proposal(policy_path, new_policy, approved=yes)
    updated = staged.model_copy(
        update={
            "status": "applied",
            "applied_at": datetime.now(timezone.utc).isoformat(),
        }
    )
    persisted = [
        updated if item.proposal_id == proposal_id else item
        for item in proposals
    ]
    save_learning_staging(state, persisted)
    _append_applied_change(state, updated)
    return applied_path


def _require_apply_allowed(staged: StagedLearningProposal, *, yes: bool) -> None:
    if not yes:
        raise PermissionError("learning-apply requires --yes for explicit approval")
    if staged.status != "staged":
        raise PermissionError(f"Proposal status is {staged.status}; cannot apply")
    if not staged.risk.replay_gate_passed:
        raise PermissionError("Replay gate did not pass; proposal remains staged")
    if staged.risk.risk_level != "low" and not staged.approved:
        raise PermissionError(
            "Only low-risk proposals can be applied non-interactively; "
            "medium/high/critical proposals require separate human approval."
        )


def _append_applied_change(state_dir: Path, staged: StagedLearningProposal) -> None:
    path = state_dir / "applied_learning_changes.json"
    payload: dict[str, Any] = {"applied": []}
    if path.exists():
        loaded = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(loaded, dict) and isinstance(loaded.get("applied"), list):
            payload = loaded
    payload["applied"].append(
        {
            "proposal_id": staged.proposal_id,
            "applied_at": staged.applied_at,
            "risk_level": staged.risk.risk_level,
        }
    )
    atomic_write_text(
        path,
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
    )
