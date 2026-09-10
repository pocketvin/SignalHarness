from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from signal_harness.cli import app
from signal_harness.golden_candidates import (
    candidate_store_path,
    load_candidates,
    record_feedback_candidate,
    write_review_draft,
)
from signal_harness.projects.state import prepare_project_state
from signal_harness.signal.schemas import FeedbackLabel

runner = CliRunner()


def _frozen_change() -> dict[str, object]:
    return {
        "change_id": "chg-1",
        "event_revision_id": 7,
        "event_id": "evt-1",
        "event": {
            "event_id": "evt-1",
            "source_type": "github_issue",
            "source_name": "owner/repo",
            "title": "A tricky upstream proposal",
            "content": "The issue is still a proposal.",
            "url": "https://example.test/issue/1",
            "collected_at": "2026-09-10T00:00:00Z",
        },
        "assessment": {
            "event_id": "evt-1",
            "category": "policy_signal",
            "relevance_score": 80,
            "impact_score": 70,
            "confidence": 0.6,
            "decision": "alert",
            "reason": "Overstated proposal impact.",
        },
    }


def test_useful_feedback_does_not_auto_create_golden_candidate(tmp_path: Path) -> None:
    state = tmp_path / "state"
    candidate = record_feedback_candidate(
        project_state=state,
        project_id="signalharness",
        run_id="run-1",
        event_id="evt-1",
        label=FeedbackLabel.USEFUL,
        note="correct",
        source="test",
        frozen_change=_frozen_change(),
    )

    assert candidate is None
    assert not candidate_store_path(state).exists()


def test_negative_feedback_freezes_run_context_and_is_idempotent(tmp_path: Path) -> None:
    state = tmp_path / "state"
    first = record_feedback_candidate(
        project_state=state,
        project_id="signalharness",
        run_id="run-1",
        event_id="evt-1",
        label=FeedbackLabel.TOO_GENERIC,
        note="why section was generic",
        source="api",
        frozen_change=_frozen_change(),
    )
    second = record_feedback_candidate(
        project_state=state,
        project_id="signalharness",
        run_id="run-1",
        event_id="evt-1",
        label=FeedbackLabel.TOO_GENERIC,
        note="duplicate click",
        source="api",
        frozen_change=_frozen_change(),
    )

    assert first is not None and second is not None
    assert first.candidate_id == second.candidate_id
    saved = load_candidates(candidate_store_path(state))
    assert len(saved) == 1
    assert saved[0].event is not None
    assert saved[0].assessment is not None
    assert saved[0].change_id == "chg-1"
    assert saved[0].event_revision_id == 7
    assert saved[0].feedback_note == "why section was generic"


def test_review_draft_requires_human_truth_before_promotion(tmp_path: Path) -> None:
    state = tmp_path / "state"
    candidate = record_feedback_candidate(
        project_state=state,
        project_id="signalharness",
        run_id="run-1",
        event_id="evt-1",
        label=FeedbackLabel.FALSE_POSITIVE,
        note="proposal was treated as implemented",
        source="api",
        frozen_change=_frozen_change(),
    )
    assert candidate is not None

    output = tmp_path / "review.json"
    write_review_draft(
        project_state=state,
        candidate_id=candidate.candidate_id,
        output_path=output,
    )
    payload = json.loads(output.read_text(encoding="utf-8"))

    assert payload["candidate"]["candidate_id"] == candidate.candidate_id
    assert payload["proposed_annotation"]["truth_status"] is None
    assert payload["proposed_annotation"]["relevance_grade"] is None
    assert payload["proposed_annotation"]["must_include_facts"] == []
    assert len(payload["review_required"]) >= 4


def test_candidate_cli_lists_and_exports_review(
    project_root: Path, tmp_path: Path
) -> None:
    state_root = tmp_path / "state"
    project_state = prepare_project_state(
        state_root, "signalharness", migrate_legacy_default=False
    )
    candidate = record_feedback_candidate(
        project_state=project_state,
        project_id="signalharness",
        run_id="run-1",
        event_id="evt-1",
        label=FeedbackLabel.FALSE_POSITIVE,
        note="real failure",
        source="test",
        frozen_change=_frozen_change(),
    )
    assert candidate is not None

    listed = runner.invoke(
        app,
        [
            "golden-candidates",
            "--cwd",
            str(project_root),
            "--state-dir",
            str(state_root),
            "--project",
            "signalharness",
            "--json",
        ],
    )
    assert listed.exit_code == 0, listed.output
    payload = json.loads(listed.output)
    assert payload[0]["candidate_id"] == candidate.candidate_id

    output_dir = tmp_path / "review-output"
    exported = runner.invoke(
        app,
        [
            "golden-review-draft",
            candidate.candidate_id,
            "--cwd",
            str(project_root),
            "--state-dir",
            str(state_root),
            "--project",
            "signalharness",
            "--output-dir",
            str(output_dir),
        ],
    )
    assert exported.exit_code == 0, exported.output
    review = output_dir / f"{candidate.candidate_id}.review.json"
    assert review.exists()
    assert json.loads(review.read_text(encoding="utf-8"))["proposed_annotation"][
        "truth_status"
    ] is None
