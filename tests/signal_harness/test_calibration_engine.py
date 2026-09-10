from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path

from fastapi.testclient import TestClient

from signal_harness.calibration import (
    CalibrationDataset,
    CalibrationEpisode,
    build_calibration_dataset,
    evaluate_calibration_replay,
)
from signal_harness.persistence import ChangeLedger
from signal_harness.service import create_app
from signal_harness.signal.policy import load_signal_policy, load_yaml_mapping
from signal_harness.signal.scorer import score_signal
from signal_harness.signal.schemas import SignalEvent


def _event(event_id: str, *, title: str, content: str, source_name: str) -> SignalEvent:
    now = datetime(2026, 9, 9, 12, 0, tzinfo=timezone.utc)
    return SignalEvent(
        event_id=event_id,
        source_type="github_release",
        source_name=source_name,
        title=title,
        content=content,
        url=f"https://example.test/{event_id}",
        published_at=now,
        change_kind="released",
        current_version="99.0.0",
        raw_payload={"official": True, "repository": source_name},
        collected_at=now,
    )


def _episode(project_id: str, index: int, event: SignalEvent, label: str) -> CalibrationEpisode:
    return CalibrationEpisode(
        episode_id=f"episode-{index}",
        project_id=project_id,
        scan_id=f"scan-{index}",
        change_id=f"chg-{index}",
        event_revision_id=index,
        event=event,
        relevance_label=label,  # type: ignore[arg-type]
        label_evidence=[f"test:{label}"],
    )


def test_service_feedback_outcome_builds_frozen_episode(
    project_root: Path,
    tmp_path: Path,
) -> None:
    output_dir = tmp_path / "outputs"
    state_dir = tmp_path / "state"
    app = create_app(
        cwd=project_root,
        output_dir=output_dir,
        state_dir=state_dir,
    )
    with TestClient(app) as client:
        run = client.post("/runs", json={"mode": "mock-agent"})
        assert run.status_code == 201, run.text
        run_id = run.json()["run_id"]

        feedback = client.post(
            "/feedback",
            json={
                "run_id": run_id,
                "signal_id": "demo-001",
                "label": "useful",
                "note": "This was genuinely useful after review.",
            },
        )
        assert feedback.status_code == 200, feedback.text

        ledger = ChangeLedger(
            state_dir / "projects" / "signalharness" / "change_ledger.sqlite3"
        )
        frozen = ledger.scan_change_for_event(scan_id=run_id, event_id="demo-001")
        assert frozen is not None
        outcome = client.post(
            "/projects/signalharness/outcomes",
            json={
                "scan_id": run_id,
                "change_id": frozen["change_id"],
                "impact_observed": True,
                "action_taken": True,
                "action_helpful": True,
                "resolved": True,
                "note": "The recommended review identified a real compatibility issue.",
            },
        )
        assert outcome.status_code == 201, outcome.text
        assert outcome.json()["event_revision_id"] == frozen["event_revision_id"]

        calibration = client.get(
            "/projects/signalharness/calibration?include_episodes=true"
        )
        assert calibration.status_code == 200, calibration.text
        payload = calibration.json()
        assert payload["feedback_count"] == 1
        assert payload["outcome_count"] == 1
        assert payload["episode_count"] == 1
        assert payload["positive_count"] == 1
        assert payload["negative_count"] == 0
        assert payload["ready_for_replay"] is False
        assert payload["episodes"][0]["change_id"] == frozen["change_id"]
        assert payload["episodes"][0]["event_revision_id"] == frozen["event_revision_id"]
        assert payload["episodes"][0]["relevance_label"] == "positive"

        outcomes = client.get("/projects/signalharness/outcomes")
        assert outcomes.status_code == 200
        assert outcomes.json()["count"] == 1


def test_calibration_dataset_keeps_conflicting_real_evidence_ambiguous(
    project_root: Path,
    tmp_path: Path,
) -> None:
    ledger = ChangeLedger(tmp_path / "ledger.sqlite3")
    profile = load_yaml_mapping(project_root / "configs/project_profile.yaml")
    policy = load_signal_policy(project_root / "configs/signal_policy.yaml")
    event = _event(
        "conflict-1",
        title="Pydantic validation compatibility change",
        content="A schema validation compatibility change.",
        source_name="pydantic/pydantic",
    )
    ledger.begin_scan(
        scan_id="scan-conflict",
        project_id="signalharness",
        collected_count=1,
        deduped_count=1,
    )
    refs = ledger.persist_observations([event])
    ledger.freeze_scan_changes(
        scan_id="scan-conflict",
        project_id="signalharness",
        events=[event],
        event_change_ids=refs,
        project_profile=profile,
        policy=policy,
        analyzed_event_ids=set(),
    )
    ledger.complete_scan(scan_id="scan-conflict", analyzed_count=0, relevant_count=1)
    frozen = ledger.scan_change_for_event(scan_id="scan-conflict", event_id=event.event_id)
    assert frozen is not None
    ledger.record_calibration_feedback(
        project_id="signalharness",
        scan_id="scan-conflict",
        change_id=str(frozen["change_id"]),
        event_revision_id=int(frozen["event_revision_id"]),
        event_id=event.event_id,
        label="useful",
        note="Initially looked useful.",
        source="test",
    )
    ledger.record_outcome(
        project_id="signalharness",
        scan_id="scan-conflict",
        change_id=str(frozen["change_id"]),
        event_revision_id=int(frozen["event_revision_id"]),
        impact_observed=False,
        action_taken=False,
        action_helpful=None,
        resolved=None,
        note="Follow-up showed no project impact.",
        source="test",
    )

    dataset = build_calibration_dataset(ledger=ledger, project_id="signalharness")
    assert dataset.feedback_count == 1
    assert dataset.outcome_count == 1
    assert dataset.ambiguous_count == 1
    assert dataset.labeled_count == 0
    assert dataset.episodes[0].relevance_label == "ambiguous"
    assert set(dataset.episodes[0].label_evidence) == {
        "feedback:useful",
        "outcome:no_impact",
    }


def test_calibration_replay_requires_evidence_and_measured_gain(project_root: Path) -> None:
    profile = load_yaml_mapping(project_root / "configs/project_profile.yaml")
    base = load_signal_policy(project_root / "configs/signal_policy.yaml")
    positive_one = _event(
        "positive-1",
        title="Pydantic security compatibility migration",
        content="Breaking security validation migration for a direct dependency.",
        source_name="pydantic/pydantic",
    )
    positive_two = _event(
        "positive-2",
        title="FastAPI breaking API compatibility migration",
        content="Breaking API compatibility change requiring migration.",
        source_name="fastapi/fastapi",
    )
    negative = _event(
        "negative-1",
        title="Unrelated ecosystem release",
        content="Routine formatting and examples with no project relevance.",
        source_name="unrelated/example",
    )
    positive_scores = [
        score_signal(event, profile, base).final_score
        for event in (positive_one, positive_two)
    ]
    negative_score = score_signal(negative, profile, base).final_score
    assert min(positive_scores) > negative_score
    episodes = [
        _episode("signalharness", 1, positive_one, "positive"),
        _episode("signalharness", 2, positive_two, "positive"),
        _episode("signalharness", 3, negative, "negative"),
    ]
    dataset = CalibrationDataset(
        project_id="signalharness",
        episodes=episodes,
        positive_count=2,
        negative_count=1,
        ambiguous_count=0,
        unlabeled_count=0,
        orphan_feedback_count=0,
        feedback_count=3,
        outcome_count=0,
    )

    old_policy = deepcopy(base)
    old_policy["thresholds"] = dict(old_policy["thresholds"])
    old_policy["thresholds"]["save"] = max(0.0, negative_score - 1.0)
    improved_policy = deepcopy(old_policy)
    improved_policy["thresholds"] = dict(improved_policy["thresholds"])
    improved_policy["thresholds"]["save"] = (
        negative_score + min(positive_scores)
    ) / 2

    insufficient = evaluate_calibration_replay(
        dataset.model_copy(
            update={
                "episodes": episodes[:1],
                "positive_count": 1,
                "negative_count": 0,
                "feedback_count": 1,
            }
        ),
        project_profile=profile,
        old_policy=old_policy,
        proposed_policy=improved_policy,
    )
    assert insufficient.recommendation == "insufficient_evidence"
    assert insufficient.promotion_allowed is False

    no_gain = evaluate_calibration_replay(
        dataset,
        project_profile=profile,
        old_policy=old_policy,
        proposed_policy=deepcopy(old_policy),
    )
    assert no_gain.recommendation == "reject_no_gain"
    assert no_gain.promotion_allowed is False

    improved = evaluate_calibration_replay(
        dataset,
        project_profile=profile,
        old_policy=old_policy,
        proposed_policy=improved_policy,
    )
    assert improved.recommendation == "review_and_consider"
    assert improved.promotion_allowed is True
    assert improved.false_positive_reduction == 1
    assert improved.missed_positive_reduction == 0

    regressed_policy = deepcopy(improved_policy)
    regressed_policy["thresholds"] = dict(regressed_policy["thresholds"])
    regressed_policy["thresholds"]["save"] = max(positive_scores) + 1.0
    regressed = evaluate_calibration_replay(
        dataset,
        project_profile=profile,
        old_policy=improved_policy,
        proposed_policy=regressed_policy,
    )
    assert regressed.recommendation == "reject_regression"
    assert regressed.promotion_allowed is False
    assert regressed.missed_positive_reduction < 0


def test_calibration_shadow_exposes_rank_decision_and_notification_changes(
    project_root: Path,
) -> None:
    from signal_harness.signal.schemas import (
        SignalAssessment,
        SignalCategory,
        SignalDecision,
        SourceQuality,
    )

    profile = load_yaml_mapping(project_root / "configs/project_profile.yaml")
    base = load_signal_policy(project_root / "configs/signal_policy.yaml")

    first = _event(
        "shadow-first",
        title="Routine dependency context",
        content="Routine upstream context.",
        source_name="pydantic/pydantic",
    )
    second = _event(
        "shadow-second",
        title="rarecalibrationtoken ecosystem note",
        content="rarecalibrationtoken changes a monitored behavior.",
        source_name="unrelated/example",
    )
    policy_event = _event(
        "shadow-notification",
        title="Permission policy change",
        content="Permission boundary policy update requires review.",
        source_name="example/policy",
    ).model_copy(
        update={
            "source_type": "github_issue",
            "change_kind": "new",
            "current_version": None,
        }
    )
    policy_assessment = SignalAssessment(
        event_id=policy_event.event_id,
        category=SignalCategory.POLICY_SIGNAL,
        relevance_score=80,
        impact_score=80,
        confidence=0.9,
        affected_modules=["permission boundary"],
        evidence_urls=[policy_event.url],
        source_quality=SourceQuality.OFFICIAL,
        reason="Policy change affects project permissions.",
        action_items=["Review permission behavior."],
        decision=SignalDecision.SAVE,
        cross_source_confidence=0.8,
    )

    ranking_old = deepcopy(base)
    ranking_old["score_weights"] = {
        "source_weight": 1.0,
        "keyword_match_score": 0.0,
        "project_relevance_score": 0.0,
        "novelty_score": 0.0,
        "urgency_score": 0.0,
        "feedback_adjustment": 0.0,
    }
    ranking_new = deepcopy(base)
    ranking_new["score_weights"] = {
        "source_weight": 0.0,
        "keyword_match_score": 1.0,
        "project_relevance_score": 0.0,
        "novelty_score": 0.0,
        "urgency_score": 0.0,
        "feedback_adjustment": 0.0,
    }
    ranking_new["keyword_weights"] = {
        **ranking_new.get("keyword_weights", {}),
        "rarecalibrationtoken": 5.0,
    }
    rank_dataset = CalibrationDataset(
        project_id="signalharness",
        episodes=[
            _episode("signalharness", 1, first, "positive"),
            _episode("signalharness", 2, second, "negative"),
        ],
        positive_count=1,
        negative_count=1,
        ambiguous_count=0,
        unlabeled_count=0,
        orphan_feedback_count=0,
        feedback_count=2,
        outcome_count=0,
    )
    rank_report = evaluate_calibration_replay(
        rank_dataset,
        project_profile=profile,
        old_policy=ranking_old,
        proposed_policy=ranking_new,
        minimum_labeled_required=2,
    )
    assert rank_report.ranking_change_count == 2
    by_id = {item.episode_id: item for item in rank_report.shadow_changes}
    assert by_id["episode-1"].old_rank == 1
    assert by_id["episode-1"].proposed_rank == 2
    assert by_id["episode-2"].old_rank == 2
    assert by_id["episode-2"].proposed_rank == 1

    policy_score = score_signal(policy_event, profile, base).final_score
    assert 2 < policy_score < 98
    old_policy = deepcopy(base)
    old_policy["thresholds"] = {
        "save": 0,
        "alert": min(99.0, policy_score + 1.0),
        "action_required": 100,
    }
    proposed_policy = deepcopy(base)
    proposed_policy["thresholds"] = {
        "save": 0,
        "alert": max(1.0, policy_score - 1.0),
        "action_required": 100,
    }
    notification_episode = _episode(
        "signalharness", 3, policy_event, "positive"
    ).model_copy(update={"assessment": policy_assessment})
    notification_dataset = CalibrationDataset(
        project_id="signalharness",
        episodes=[notification_episode],
        positive_count=1,
        negative_count=0,
        ambiguous_count=0,
        unlabeled_count=0,
        orphan_feedback_count=0,
        feedback_count=1,
        outcome_count=0,
    )
    notification_report = evaluate_calibration_replay(
        notification_dataset,
        project_profile=profile,
        old_policy=old_policy,
        proposed_policy=proposed_policy,
        minimum_labeled_required=1,
    )
    shadow = notification_report.shadow_changes[0]
    assert shadow.old_decision == "save"
    assert shadow.proposed_decision == "alert"
    assert shadow.decision_changed is True
    assert shadow.old_notification_eligible is False
    assert shadow.proposed_notification_eligible is True
    assert shadow.notification_changed is True
    assert notification_report.notification_change_count == 1
