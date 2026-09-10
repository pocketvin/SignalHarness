from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path

from signal_harness.agent_integration.scoring_bridge import compute_guarded_score_decision
from signal_harness.capability_eval import load_capability_suite
from signal_harness.signal.policy import load_signal_policy, load_yaml_mapping
from signal_harness.signal.schemas import SignalCategory, SignalDecision, SignalEvent, SourceQuality


def _context(project_root: Path) -> tuple[dict[str, object], dict[str, object]]:
    return (
        load_yaml_mapping(project_root / "configs/project_profile.yaml"),
        load_signal_policy(project_root / "configs/signal_policy.yaml"),
    )


def _case(project_root: Path, case_id: str):
    suite = load_capability_suite(
        project_root / "examples/signal_harness/capability_golden_v1.json"
    )
    return next(item for item in suite.cases if item.id == case_id)


def _score_case(
    project_root: Path,
    case_id: str,
    *,
    semantic_relevance: float,
    confidence: float,
):
    profile, policy = _context(project_root)
    case = _case(project_root, case_id)
    return compute_guarded_score_decision(
        case.event,
        category=SignalCategory(case.expected.expected_category),
        analyze=True,
        source_quality=case.evidence.source_quality,
        evidence_confidence=confidence,
        evidence_uncertainty=case.evidence.uncertainty,
        evidence_unsupported_claims=case.evidence.unsupported_claims,
        semantic_relevance=semantic_relevance,
        project_profile=profile,
        policy=policy,
    )


def test_uncertain_security_signal_does_not_receive_priority_alert_floor(
    project_root: Path,
) -> None:
    scored = _score_case(project_root, "cap-007", semantic_relevance=64, confidence=0.72)

    assert scored.final_score < 80
    assert scored.decision in {SignalDecision.IGNORE, SignalDecision.SAVE}


def test_unmerged_protocol_proposal_does_not_receive_project_alert_floor(
    project_root: Path,
) -> None:
    scored = _score_case(project_root, "cap-009", semantic_relevance=85, confidence=0.70)

    assert scored.final_score < 80
    assert scored.decision is SignalDecision.SAVE


def test_verified_official_direct_impact_can_receive_semantic_alert_floor(
    project_root: Path,
) -> None:
    scored = _score_case(project_root, "cap-005", semantic_relevance=92, confidence=0.90)

    assert scored.final_score >= 80
    assert scored.decision is SignalDecision.ALERT


def test_high_relevance_secondary_engineering_guidance_is_saved_not_alerted(
    project_root: Path,
) -> None:
    scored = _score_case(project_root, "cap-021", semantic_relevance=85, confidence=0.60)

    assert 45 <= scored.final_score < 80
    assert scored.decision is SignalDecision.SAVE


def test_low_confidence_community_opinion_stays_ignore(project_root: Path) -> None:
    profile, policy = _context(project_root)
    case = _case(project_root, "cap-022")
    scored = compute_guarded_score_decision(
        case.event,
        category=SignalCategory.EXPERT_OPINION,
        analyze=True,
        source_quality=SourceQuality.COMMUNITY,
        evidence_confidence=0.20,
        evidence_uncertainty="Opinion only; no project change is verified.",
        evidence_unsupported_claims=["No implementation evidence."],
        semantic_relevance=65,
        project_profile=profile,
        policy=policy,
    )

    assert scored.decision is SignalDecision.IGNORE


def test_already_satisfied_dependency_overrides_semantic_alert_floor(
    project_root: Path,
) -> None:
    profile, policy = _context(project_root)
    event = SignalEvent(
        event_id="already-satisfied",
        source_type="package_registry",
        source_name="pydantic",
        title="Pydantic 2.13.4 security compatibility migration",
        content="Security compatibility migration and schema validation update.",
        current_version="2.13.4",
        collected_at=datetime.now(timezone.utc),
        raw_payload={"entity_type": "dependency", "entity_name": "pydantic"},
    )
    scored = compute_guarded_score_decision(
        event,
        category=SignalCategory.DEPENDENCY_UPDATE,
        analyze=True,
        source_quality=SourceQuality.OFFICIAL,
        evidence_confidence=0.99,
        evidence_uncertainty="",
        evidence_unsupported_claims=[],
        semantic_relevance=99,
        project_profile=profile,
        policy=policy,
    )

    assert scored.final_score >= 80
    assert scored.decision is SignalDecision.IGNORE


def test_explicit_ignore_preference_overrides_semantic_alert_floor(project_root: Path) -> None:
    profile, policy = _context(project_root)
    ignored_profile = deepcopy(profile)
    preferences = list(ignored_profile.get("importance_preferences", []))
    preferences.append(
        {"scope_type": "dependency", "scope_key": "pydantic", "importance": "ignore"}
    )
    ignored_profile["importance_preferences"] = preferences
    event = SignalEvent(
        event_id="ignored-preference",
        source_type="package_registry",
        source_name="pydantic",
        title="Pydantic 3.0 breaking schema migration",
        content="Breaking schema migration and compatibility change.",
        current_version="3.0.0",
        collected_at=datetime.now(timezone.utc),
        raw_payload={"entity_type": "dependency", "entity_name": "pydantic"},
    )
    scored = compute_guarded_score_decision(
        event,
        category=SignalCategory.DEPENDENCY_UPDATE,
        analyze=True,
        source_quality=SourceQuality.OFFICIAL,
        evidence_confidence=0.99,
        evidence_uncertainty="",
        evidence_unsupported_claims=[],
        semantic_relevance=99,
        project_profile=ignored_profile,
        policy=policy,
    )

    assert scored.final_score >= 80
    assert scored.decision is SignalDecision.IGNORE


def test_corroborated_floor_distinguishes_strong_project_match_from_weaker_breaking_signal(
    project_root: Path,
) -> None:
    import json

    profile, policy = _context(project_root)
    events = {
        item["event_id"]: SignalEvent.model_validate(item)
        for item in json.loads(
            (project_root / "examples/signal_harness/regression_events.json").read_text(
                encoding="utf-8"
            )
        )
    }
    stronger = compute_guarded_score_decision(
        events["reg-006"],
        category=SignalCategory.DEPENDENCY_UPDATE,
        analyze=True,
        source_quality=SourceQuality.OFFICIAL,
        evidence_confidence=0.90,
        evidence_uncertainty="",
        evidence_unsupported_claims=[],
        semantic_relevance=82,
        project_profile=profile,
        policy=policy,
    )
    weaker = compute_guarded_score_decision(
        events["reg-003"],
        category=SignalCategory.DEPENDENCY_UPDATE,
        analyze=True,
        source_quality=SourceQuality.OFFICIAL,
        evidence_confidence=0.90,
        evidence_uncertainty="",
        evidence_unsupported_claims=[],
        semantic_relevance=82,
        project_profile=profile,
        policy=policy,
    )

    assert stronger.agent_score_breakdown.deterministic_base_score >= 80
    assert stronger.decision is SignalDecision.ALERT
    assert weaker.agent_score_breakdown.deterministic_base_score < 80
    assert weaker.decision is SignalDecision.SAVE
