from __future__ import annotations

from datetime import datetime, timezone

from signal_harness.signal.feedback import create_feedback_record
from signal_harness.signal.normalizer import normalize_event
from signal_harness.signal.policy import load_signal_policy
from signal_harness.signal.policy import decision_for_score
from signal_harness.signal.scorer import score_signal
from signal_harness.signal.schemas import SignalCategory


NOW = datetime(2026, 6, 25, tzinfo=timezone.utc)
PROFILE = {
    "tech_stack": ["Python"],
    "critical_modules": ["checkpoint"],
    "dependencies": ["pydantic"],
    "monitored_ecosystem": ["langgraph"],
    "competitors": ["CrewAI"],
    "focus_keywords": ["checkpoint", "persistence"],
    "ignore_keywords": ["giveaway"],
}


def _event(
    title: str,
    *,
    source_type: str = "github_release",
    source_name: str = "langchain-ai/langgraph",
    official: bool = True,
):
    return normalize_event(
        {
            "event_id": title.lower().replace(" ", "-"),
            "source_type": source_type,
            "source_name": source_name,
            "title": title,
            "content": title,
            "url": "https://example.com/item",
            "raw_payload": {"official": official},
            "collected_at": NOW,
            "published_at": NOW,
        }
    )


def test_focus_keywords_raise_score(project_root) -> None:
    policy = load_signal_policy(project_root / "configs" / "signal_policy.yaml")
    focused = score_signal(_event("checkpoint persistence"), PROFILE, policy, now=NOW)
    generic = score_signal(_event("minor documentation"), PROFILE, policy, now=NOW)

    assert focused.keyword_match_score > generic.keyword_match_score
    assert focused.final_score > generic.final_score


def test_ignore_keywords_lower_score(project_root) -> None:
    policy = load_signal_policy(project_root / "configs" / "signal_policy.yaml")
    ignored = score_signal(_event("checkpoint giveaway"), PROFILE, policy, now=NOW)
    focused = score_signal(_event("checkpoint persistence"), PROFILE, policy, now=NOW)

    assert ignored.keyword_match_score < focused.keyword_match_score


def test_higher_source_weight_raises_score(project_root) -> None:
    policy = load_signal_policy(project_root / "configs" / "signal_policy.yaml")
    official = score_signal(_event("checkpoint update"), PROFILE, policy, now=NOW)
    unverified = score_signal(
        _event("checkpoint update", source_type="unknown", official=False),
        PROFILE,
        policy,
        now=NOW,
    )

    assert official.source_weight > unverified.source_weight
    assert official.final_score > unverified.final_score


def test_feedback_adjustment_affects_future_score(project_root) -> None:
    policy = load_signal_policy(project_root / "configs" / "signal_policy.yaml")
    event = _event("checkpoint update")
    baseline = score_signal(event, PROFILE, policy, now=NOW)
    feedback = create_feedback_record(event.event_id, "useful", "checkpoint")
    adjusted = score_signal(
        event,
        PROFILE,
        policy,
        feedback_history=[feedback],
        now=NOW,
    )

    assert adjusted.feedback_adjustment > baseline.feedback_adjustment
    assert adjusted.final_score > baseline.final_score


def test_suggested_focus_keyword_is_used_by_scorer(project_root) -> None:
    policy = load_signal_policy(project_root / "configs" / "signal_policy.yaml")
    event = _event("observability pipeline")
    baseline = score_signal(event, PROFILE, policy, now=NOW)
    adaptive = {
        **policy,
        "suggested_focus_keywords": ["observability"],
        "keyword_weights": {**policy["keyword_weights"], "observability": 1.4},
    }

    adjusted = score_signal(event, PROFILE, adaptive, now=NOW)

    assert adjusted.keyword_match_score > baseline.keyword_match_score
    assert adjusted.final_score > baseline.final_score


def test_ignore_pattern_lowers_score(project_root) -> None:
    policy = load_signal_policy(project_root / "configs" / "signal_policy.yaml")
    event = _event("marketing fluff update")
    baseline = score_signal(event, PROFILE, policy, now=NOW)
    adaptive = {**policy, "ignore_patterns": [*policy["ignore_patterns"], "marketing"]}

    adjusted = score_signal(event, PROFILE, adaptive, now=NOW)

    assert adjusted.keyword_match_score < baseline.keyword_match_score
    assert adjusted.final_score < baseline.final_score


def test_category_weight_affects_final_priority(project_root) -> None:
    policy = load_signal_policy(project_root / "configs" / "signal_policy.yaml")
    event = _event("checkpoint persistence")

    dependency = score_signal(
        event,
        PROFILE,
        policy,
        now=NOW,
        category=SignalCategory.DEPENDENCY_UPDATE,
    )
    market = score_signal(
        event,
        PROFILE,
        policy,
        now=NOW,
        category=SignalCategory.MARKET_SIGNAL,
    )

    assert dependency.category_weight > market.category_weight
    assert dependency.final_score > market.final_score


def test_plain_github_release_defaults_to_save_not_alert(project_root) -> None:
    policy = load_signal_policy(project_root / "configs" / "signal_policy.yaml")
    release = score_signal(
        _event("langgraph==1.2.7"),
        PROFILE,
        policy,
        now=NOW,
        category=SignalCategory.DEPENDENCY_UPDATE,
    )

    assert decision_for_score(release.final_score, policy).value in {"ignore", "save"}


def test_security_breaking_dependency_can_require_action(project_root) -> None:
    policy = load_signal_policy(project_root / "configs" / "signal_policy.yaml")
    event = _event(
        "pydantic security breaking API compatibility CVE checkpoint persistence migration"
    )
    score = score_signal(
        event,
        PROFILE,
        policy,
        now=NOW,
        category=SignalCategory.SECURITY_SUPPLY_CHAIN,
    )

    assert decision_for_score(score.final_score, policy).value in {
        "alert",
        "action_required",
    }


def test_monitored_ecosystem_scores_below_direct_dependency(project_root) -> None:
    policy = load_signal_policy(project_root / "configs" / "signal_policy.yaml")
    dependency = score_signal(
        _event(
            "pydantic JSON schema compatibility migration",
            source_name="pydantic/pydantic",
        ),
        PROFILE,
        policy,
        now=NOW,
    )
    ecosystem = score_signal(
        _event("langgraph JSON schema compatibility migration"),
        PROFILE,
        policy,
        now=NOW,
    )

    assert dependency.project_relevance_score > ecosystem.project_relevance_score


def test_dependency_relevance_does_not_match_inside_words(project_root) -> None:
    policy = load_signal_policy(project_root / "configs" / "signal_policy.yaml")
    clean = score_signal(
        _event("descriptive issue with validation notes"),
        {
            **PROFILE,
            "dependencies": ["rich"],
            "monitored_ecosystem": [],
            "focus_keywords": [],
            "critical_modules": [],
        },
        policy,
        now=NOW,
    )
    direct = score_signal(
        _event(
            "rich compatibility regression",
            source_name="Textualize/rich",
        ),
        {
            **PROFILE,
            "dependencies": ["rich"],
            "monitored_ecosystem": [],
            "focus_keywords": [],
            "critical_modules": [],
        },
        policy,
        now=NOW,
    )

    assert direct.project_relevance_score > clean.project_relevance_score


def test_github_issue_body_references_do_not_inflate_keyword_relevance(project_root) -> None:
    policy = load_signal_policy(project_root / "configs" / "signal_policy.yaml")
    event = normalize_event(
        {
            "event_id": "store-filter-noise",
            "source_type": "github_issue",
            "source_name": "langchain-ai/langgraph",
            "title": "Store filter combinators return inconsistent results",
            "content": (
                "This report is about store filtering. A related issue mentions checkpoint "
                "persistence migration, but that is not the reported failure."
            ),
            "url": "https://github.com/langchain-ai/langgraph/issues/1",
            "raw_payload": {"official": True},
            "collected_at": NOW,
            "published_at": NOW,
        }
    )
    score = score_signal(event, PROFILE, policy, now=NOW)

    assert score.keyword_match_score == 20.0
    assert score.project_relevance_score < 40.0


def test_github_issue_title_can_match_runtime_focus_without_body_scan(project_root) -> None:
    policy = load_signal_policy(project_root / "configs" / "signal_policy.yaml")
    profile = {
        **PROFILE,
        "focus_keywords": ["event loop"],
        "critical_modules": ["provider adapter"],
    }
    event = normalize_event(
        {
            "event_id": "event-loop-blocking",
            "source_type": "github_issue",
            "source_name": "openai/openai-python",
            "title": "Blocking the event loop since 3.x?",
            "content": "A long issue template contains unrelated checkpoint references.",
            "url": "https://github.com/openai/openai-python/issues/1",
            "raw_payload": {"official": True},
            "collected_at": NOW,
            "published_at": NOW,
        }
    )
    score = score_signal(event, profile, policy, now=NOW)

    assert score.keyword_match_score > 20.0
    assert score.project_relevance_score >= 25.0
