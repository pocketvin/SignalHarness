from __future__ import annotations

from signal_harness.signal.feedback import (
    create_feedback_record,
    generate_policy_proposal,
    load_feedback_history,
    save_feedback,
)
from signal_harness.signal.policy import apply_policy_proposal, load_signal_policy
from signal_harness.signal.scorer import score_signal
from signal_harness.signal.normalizer import normalize_event


def test_useful_feedback_is_saved(tmp_path) -> None:
    path = tmp_path / "feedback_memory.json"
    record = create_feedback_record("demo-001", "useful", "checkpoint matters")

    save_feedback(path, record)

    assert load_feedback_history(path) == [record]


def test_false_positive_generates_policy_proposal(project_root) -> None:
    policy = load_signal_policy(project_root / "configs" / "signal_policy.yaml")
    record = create_feedback_record("demo-001", "false_positive")

    proposal = generate_policy_proposal([record], policy)

    assert proposal.requires_approval is True
    assert proposal.new_policy["thresholds"]["alert"] > policy["thresholds"]["alert"]


def test_feedback_does_not_overwrite_policy(tmp_path, project_root) -> None:
    source = project_root / "configs" / "signal_policy.yaml"
    policy_copy = tmp_path / "signal_policy.yaml"
    original = source.read_text(encoding="utf-8")
    policy_copy.write_text(original, encoding="utf-8")
    feedback_path = tmp_path / "feedback_memory.json"

    save_feedback(
        feedback_path,
        create_feedback_record("demo-001", "false_positive"),
    )

    assert policy_copy.read_text(encoding="utf-8") == original


def test_useful_proposal_changes_future_scoring(project_root, tmp_path) -> None:
    policy = load_signal_policy(project_root / "configs" / "signal_policy.yaml")
    event = normalize_event(
        {
            "event_id": "adaptive-useful",
            "source_type": "rss",
            "source_name": "Expert",
            "title": "Observability architecture",
            "collected_at": "2026-06-25T00:00:00Z",
        }
    )
    profile = {"focus_keywords": [], "ignore_keywords": []}
    baseline = score_signal(event, profile, policy)
    proposal = generate_policy_proposal(
        [create_feedback_record("old", "useful", "observability")],
        policy,
    )
    policy_path = tmp_path / "signal_policy.yaml"
    apply_policy_proposal(policy_path, proposal.new_policy, approved=True)
    applied_policy = load_signal_policy(policy_path)

    adjusted = score_signal(event, profile, applied_policy)

    assert "observability" in applied_policy["suggested_focus_keywords"]
    assert adjusted.final_score > baseline.final_score


def test_false_positive_proposal_lowers_future_scoring(project_root, tmp_path) -> None:
    policy = load_signal_policy(project_root / "configs" / "signal_policy.yaml")
    event = normalize_event(
        {
            "event_id": "adaptive-negative",
            "source_type": "rss",
            "source_name": "Expert",
            "title": "Marketing campaign update",
            "collected_at": "2026-06-25T00:00:00Z",
        }
    )
    profile = {"focus_keywords": [], "ignore_keywords": []}
    baseline = score_signal(event, profile, policy)
    proposal = generate_policy_proposal(
        [create_feedback_record("old", "false_positive", "marketing")],
        policy,
    )
    policy_path = tmp_path / "signal_policy.yaml"
    apply_policy_proposal(policy_path, proposal.new_policy, approved=True)
    applied_policy = load_signal_policy(policy_path)

    adjusted = score_signal(event, profile, applied_policy)

    assert "marketing" in applied_policy["ignore_patterns"]
    assert adjusted.final_score < baseline.final_score
