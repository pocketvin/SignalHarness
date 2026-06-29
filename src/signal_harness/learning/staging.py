"""Local state files for review-first learning staging."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

from signal_harness.agent_integration.schemas import LearningPolicyOutput, ReplayEvaluation
from signal_harness.learning.proposal_risk import (
    ProposalRiskClassifier,
    ProposalRiskReport,
)
from signal_harness.utils.fs import atomic_write_text

StagedStatus = Literal["staged", "blocked", "applied", "rejected"]


class StagedLearningProposal(BaseModel):
    """One local staged learning proposal bundle."""

    model_config = ConfigDict(extra="forbid")

    proposal_id: str
    status: StagedStatus
    created_at: str
    applied_at: str | None = None
    approved: bool = False
    risk: ProposalRiskReport
    learning: dict[str, Any]
    replay_evaluation: dict[str, Any] | None = None


def load_learning_staging(state_dir: str | Path) -> list[StagedLearningProposal]:
    """Load `.signal-harness/learning_staging.json` if present."""

    path = Path(state_dir).expanduser().resolve() / "learning_staging.json"
    if not path.exists():
        return []
    payload = json.loads(path.read_text(encoding="utf-8"))
    proposals = payload.get("proposals", []) if isinstance(payload, dict) else []
    if not isinstance(proposals, list):
        return []
    return [StagedLearningProposal.model_validate(item) for item in proposals]


def stage_learning_proposal(
    *,
    state_dir: str | Path,
    output_dir: str | Path,
    learning: LearningPolicyOutput,
    replay: ReplayEvaluation | dict[str, Any] | None,
) -> StagedLearningProposal:
    """Stage a learning proposal without applying it."""

    state = Path(state_dir).expanduser().resolve()
    output = Path(output_dir).expanduser().resolve()
    state.mkdir(parents=True, exist_ok=True)
    output.mkdir(parents=True, exist_ok=True)
    risk = ProposalRiskClassifier().classify(learning, replay)
    replay_payload = (
        replay.model_dump(mode="json")
        if isinstance(replay, ReplayEvaluation)
        else replay
    )
    staged = StagedLearningProposal(
        proposal_id=learning.policy_update_proposal.proposal_id,
        status="staged" if risk.auto_stage_allowed else "blocked",
        created_at=datetime.now(timezone.utc).isoformat(),
        risk=risk,
        learning=learning.model_dump(mode="json"),
        replay_evaluation=replay_payload,
    )
    existing = [
        item
        for item in load_learning_staging(state)
        if item.proposal_id != staged.proposal_id
    ]
    proposals = [*existing, staged]
    _write_staging(state, proposals)
    atomic_write_text(
        output / "latest_learning_staging.json",
        json.dumps(
            {"proposals": [item.model_dump(mode="json") for item in proposals]},
            indent=2,
            ensure_ascii=False,
        )
        + "\n",
    )
    risk_report = render_learning_risk_report(staged)
    atomic_write_text(state / "learning_risk_report.md", risk_report)
    atomic_write_text(output / "latest_learning_risk_report.md", risk_report)
    return staged


def save_learning_staging(
    state_dir: str | Path,
    proposals: list[StagedLearningProposal],
) -> None:
    """Persist staged proposals to the state source of truth."""

    _write_staging(Path(state_dir).expanduser().resolve(), proposals)


def render_learning_risk_report(staged: StagedLearningProposal) -> str:
    """Render a short Markdown risk report for dashboard/demo review."""

    lines = [
        "# SignalHarness Learning Risk Report",
        "",
        f"- proposal_id: {staged.proposal_id}",
        f"- status: {staged.status}",
        f"- risk_level: {staged.risk.risk_level}",
        f"- auto_stage_allowed: {str(staged.risk.auto_stage_allowed).lower()}",
        f"- apply_requires_approval: {str(staged.risk.apply_requires_approval).lower()}",
        f"- replay_gate_passed: {str(staged.risk.replay_gate_passed).lower()}",
        "",
        "## Reasons",
        "",
    ]
    lines.extend(f"- {reason}" for reason in staged.risk.reasons)
    lines.extend(
        [
            "",
            "Learning staging only records reviewable proposals. It does not apply "
            "policy, watchlist, or skill changes automatically.",
        ]
    )
    return "\n".join(lines).rstrip() + "\n"


def _write_staging(
    state_dir: Path,
    proposals: list[StagedLearningProposal],
) -> None:
    state_dir.mkdir(parents=True, exist_ok=True)
    atomic_write_text(
        state_dir / "learning_staging.json",
        json.dumps(
            {"proposals": [item.model_dump(mode="json") for item in proposals]},
            indent=2,
            ensure_ascii=False,
        )
        + "\n",
    )
