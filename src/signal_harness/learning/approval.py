"""Explicit apply gate for staged learning proposals."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

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
    _require_durable_calibration_gate(state)
    policy_path = config / "signal_policy.yaml"
    active_policy = load_signal_policy(policy_path)
    SignalPermissionGuard(active_policy).require("modify_signal_policy", confirmed=yes)
    new_policy = staged.learning["policy_update_proposal"]["new_policy"]
    if not isinstance(new_policy, dict):
        raise ValueError("Staged proposal is missing a new_policy mapping")
    revision_id = _write_policy_revision(
        state_dir=state,
        proposal_id=staged.proposal_id,
        old_policy=active_policy,
        new_policy=new_policy,
    )
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
    _append_applied_change(state, updated, revision_id=revision_id)
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


def _require_durable_calibration_gate(state_dir: Path) -> None:
    ledger_path = state_dir / "change_ledger.sqlite3"
    if not ledger_path.exists():
        return
    replay_path = state_dir / "calibration_replay.json"
    if not replay_path.exists():
        raise PermissionError(
            "Durable calibration replay is required before applying a project policy proposal"
        )
    payload = json.loads(replay_path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or payload.get("promotion_allowed") is not True:
        recommendation = (
            str(payload.get("recommendation") or "unknown")
            if isinstance(payload, dict)
            else "invalid"
        )
        raise PermissionError(
            f"Durable calibration gate did not pass: {recommendation}"
        )


def _write_policy_revision(
    *,
    state_dir: Path,
    proposal_id: str,
    old_policy: dict[str, Any],
    new_policy: dict[str, Any],
) -> str:
    revision_id = f"policy-revision-{uuid4().hex[:12]}"
    root = state_dir / "policy_revisions"
    root.mkdir(parents=True, exist_ok=True)
    payload = {
        "revision_id": revision_id,
        "proposal_id": proposal_id,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "old_policy": old_policy,
        "new_policy": new_policy,
        "rolled_back_at": None,
    }
    atomic_write_text(
        root / f"{revision_id}.json",
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
    )
    return revision_id


def rollback_policy_revision(
    *,
    state_dir: str | Path,
    config_dir: str | Path,
    revision_id: str,
    yes: bool,
) -> Path:
    if not yes:
        raise PermissionError("policy rollback requires --yes for explicit approval")
    state = Path(state_dir).expanduser().resolve()
    config = Path(config_dir).expanduser().resolve()
    revision_path = state / "policy_revisions" / f"{revision_id}.json"
    if not revision_path.is_file():
        raise ValueError(f"Unknown policy revision: {revision_id}")
    payload = json.loads(revision_path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("Policy revision is invalid")
    if payload.get("rolled_back_at"):
        raise PermissionError("Policy revision has already been rolled back")
    old_policy = payload.get("old_policy")
    new_policy = payload.get("new_policy")
    if not isinstance(old_policy, dict) or not isinstance(new_policy, dict):
        raise ValueError("Policy revision is missing old/new policy state")
    policy_path = config / "signal_policy.yaml"
    active = load_signal_policy(policy_path)
    if active != new_policy:
        raise PermissionError(
            "Active policy no longer matches this revision; refusing to overwrite newer changes"
        )
    SignalPermissionGuard(active).require("modify_signal_policy", confirmed=True)
    result = apply_policy_proposal(policy_path, old_policy, approved=True)
    payload["rolled_back_at"] = datetime.now(timezone.utc).isoformat()
    atomic_write_text(
        revision_path,
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
    )
    history_path = state / "policy_rollback_history.json"
    history: list[dict[str, Any]] = []
    if history_path.exists():
        loaded = json.loads(history_path.read_text(encoding="utf-8"))
        if isinstance(loaded, list):
            history = [item for item in loaded if isinstance(item, dict)]
    history.append(
        {
            "revision_id": revision_id,
            "rolled_back_at": payload["rolled_back_at"],
        }
    )
    atomic_write_text(
        history_path,
        json.dumps(history, indent=2, ensure_ascii=False) + "\n",
    )
    return result


def _append_applied_change(
    state_dir: Path, staged: StagedLearningProposal, *, revision_id: str
) -> None:
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
            "policy_revision_id": revision_id,
        }
    )
    atomic_write_text(
        path,
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
    )
