"""Human-review queue for turning real SignalHarness failures into future Golden cases."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

from signal_harness.signal.schemas import FeedbackLabel
from signal_harness.utils.fs import atomic_write_text

CandidateStatus = Literal["candidate", "reviewed", "rejected", "promoted"]
_CANDIDATE_LABELS = {
    FeedbackLabel.NOT_USEFUL,
    FeedbackLabel.FALSE_POSITIVE,
    FeedbackLabel.TOO_GENERIC,
    FeedbackLabel.MISSED_SIGNAL,
}


class GoldenCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    candidate_id: str
    created_at: str
    project_id: str
    run_id: str | None = None
    change_id: str | None = None
    event_revision_id: int | None = None
    event_id: str
    feedback_label: FeedbackLabel
    feedback_note: str = ""
    source: str
    event: dict[str, Any] | None = None
    assessment: dict[str, Any] | None = None
    status: CandidateStatus = "candidate"


class GoldenReviewDraft(BaseModel):
    """Review worksheet; intentionally not a valid Capability Golden case yet."""

    model_config = ConfigDict(extra="forbid")

    candidate: GoldenCandidate
    review_required: list[str]
    proposed_annotation: dict[str, Any]


def candidate_store_path(project_state: Path) -> Path:
    return project_state / "golden_candidates.json"


def load_candidates(path: Path) -> list[GoldenCandidate]:
    if not path.exists():
        return []
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        raise ValueError("golden candidate store must be a JSON list")
    return [GoldenCandidate.model_validate(item) for item in payload]


def _candidate_id(
    *, project_id: str, run_id: str | None, event_id: str, label: FeedbackLabel
) -> str:
    raw = f"{project_id}|{run_id or 'none'}|{event_id}|{label.value}"
    return "golden-candidate-" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def record_feedback_candidate(
    *,
    project_state: Path,
    project_id: str,
    run_id: str | None,
    event_id: str,
    label: FeedbackLabel,
    note: str,
    source: str,
    frozen_change: dict[str, Any] | None,
) -> GoldenCandidate | None:
    """Queue negative/ambiguous user feedback for human Golden review.

    Useful feedback is deliberately excluded: a production success is not automatically a
    Golden capability case. Likewise candidates never mutate the canonical Golden suite.
    """

    if label not in _CANDIDATE_LABELS:
        return None
    path = candidate_store_path(project_state)
    existing = load_candidates(path)
    candidate_id = _candidate_id(
        project_id=project_id,
        run_id=run_id,
        event_id=event_id,
        label=label,
    )
    for item in existing:
        if item.candidate_id == candidate_id:
            return item
    frozen = frozen_change or {}
    candidate = GoldenCandidate(
        candidate_id=candidate_id,
        created_at=datetime.now(timezone.utc).isoformat(),
        project_id=project_id,
        run_id=run_id,
        change_id=(str(frozen.get("change_id")) if frozen.get("change_id") else None),
        event_revision_id=(
            int(frozen["event_revision_id"])
            if frozen.get("event_revision_id") is not None
            else None
        ),
        event_id=event_id,
        feedback_label=label,
        feedback_note=note,
        source=source,
        event=(dict(frozen["event"]) if isinstance(frozen.get("event"), dict) else None),
        assessment=(
            dict(frozen["assessment"])
            if isinstance(frozen.get("assessment"), dict)
            else None
        ),
    )
    project_state.mkdir(parents=True, exist_ok=True)
    serialized = [*existing, candidate]
    # Bound the local review queue without silently deleting unresolved recent cases.
    if len(serialized) > 1000:
        resolved = [item for item in serialized if item.status != "candidate"]
        unresolved = [item for item in serialized if item.status == "candidate"]
        serialized = [*resolved[-500:], *unresolved[-500:]]
    atomic_write_text(
        path,
        json.dumps(
            [item.model_dump(mode="json") for item in serialized],
            indent=2,
            ensure_ascii=False,
        )
        + "\n",
    )
    return candidate


def review_draft(candidate: GoldenCandidate) -> GoldenReviewDraft:
    """Create a worksheet that makes missing human truth explicit."""

    return GoldenReviewDraft(
        candidate=candidate,
        review_required=[
            "Verify the primary-source facts and mark truth_status.",
            "Decide the 0-3 project relevance grade and acceptable decisions.",
            "Write semantic must-include fact criteria rather than a reference answer string.",
            "Define project-specificity concepts, acceptable actions, and forbidden claims/actions.",
            "Add uncertainty and trajectory requirements when the failure involved evidence or tool behavior.",
        ],
        proposed_annotation={
            "truth_status": None,
            "relevance_grade": None,
            "acceptable_decisions": [],
            "expected_category": None,
            "must_include_facts": [],
            "project_concepts": [],
            "acceptable_action_concepts": [],
            "forbidden_claims": [],
            "forbidden_actions": [],
            "require_uncertainty": None,
            "trajectory": {},
        },
    )


def write_review_draft(
    *, project_state: Path, candidate_id: str, output_path: Path
) -> Path:
    candidates = {item.candidate_id: item for item in load_candidates(candidate_store_path(project_state))}
    candidate = candidates.get(candidate_id)
    if candidate is None:
        raise ValueError(f"Unknown Golden candidate: {candidate_id}")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(
        output_path,
        json.dumps(review_draft(candidate).model_dump(mode="json"), indent=2, ensure_ascii=False)
        + "\n",
    )
    return output_path
