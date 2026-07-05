from __future__ import annotations

from datetime import datetime, timezone

import pytest

from signal_harness.agents import ClassifierAgent, EvidenceAgent, SupervisorAgent
from signal_harness.runtime import (
    SignalToolExecutor,
    TraceRecorder,
    create_signal_tool_registry,
)
from signal_harness.signal.policy import load_signal_policy
from signal_harness.signal.schemas import SignalCategory, SignalEvent


def _event(**updates) -> SignalEvent:
    payload = {
        "event_id": "demo-001",
        "source_type": "github_release",
        "source_name": "langchain-ai/langgraph",
        "title": "Checkpoint persistence migration",
        "content": "A migration helper for durable checkpoint storage.",
        "url": "https://example.com/release",
        "published_at": datetime.now(timezone.utc),
        "raw_payload": {"official": True},
        "collected_at": datetime.now(timezone.utc),
    }
    payload.update(updates)
    return SignalEvent.model_validate(payload)


def test_classifier_detects_dependency_update() -> None:
    result = ClassifierAgent().run(
        _event(
            source_name="pydantic/pydantic",
            title="Pydantic JSON schema compatibility migration",
            content="A migration release changes validation and JSON schema compatibility.",
        ),
        {"dependencies": ["pydantic"], "ignore_keywords": []},
    )

    assert result.category is SignalCategory.DEPENDENCY_UPDATE


def test_classifier_does_not_treat_plain_release_as_dependency_update() -> None:
    result = ClassifierAgent().run(
        _event(
            source_name="pydantic/pydantic",
            title="Pydantic 2.12.1 release",
            content="Routine bug fixes and documentation updates.",
        ),
        {"dependencies": ["pydantic"], "ignore_keywords": []},
    )

    assert result.category is SignalCategory.ECOSYSTEM_ISSUE


def test_classifier_does_not_match_short_dependency_inside_words() -> None:
    result = ClassifierAgent().run(
        _event(
            source_type="github_issue",
            source_name="langchain-ai/langgraph",
            title="Type annotation inconsistency and descriptive bug report",
            content="A descriptive issue mentions validation without naming the package.",
        ),
        {"dependencies": ["rich"], "ignore_keywords": []},
    )

    assert result.category is not SignalCategory.DEPENDENCY_UPDATE


def test_classifier_does_not_treat_plain_github_issue_as_dependency_update() -> None:
    result = ClassifierAgent().run(
        _event(
            source_type="github_issue",
            title="FuturesDict callback does extra work",
            content="A runtime bug report about callback overhead in one component.",
        ),
        {"dependencies": ["pydantic"], "ignore_keywords": []},
    )

    assert result.category is SignalCategory.AGENT_RUNTIME_SIGNAL


def test_classifier_routes_tool_schema_provider_signals() -> None:
    classifier = ClassifierAgent()
    profile = {
        "dependencies": ["pydantic"],
        "monitored_ecosystem": ["langgraph"],
        "ignore_keywords": [],
    }

    assert (
        classifier.run(
            _event(
                source_type="github_issue",
                title="Structured output JSON schema incompatibility",
                content="with_structured_output fails when reasoning is enabled.",
            ),
            profile,
        ).category
        is SignalCategory.STRUCTURED_OUTPUT_SIGNAL
    )
    assert (
        classifier.run(
            _event(
                source_type="github_issue",
                title="Tool calling wrapper swallows interrupts",
                content="Tool call wrapper behavior prevents propagation.",
            ),
            profile,
        ).category
        is SignalCategory.TOOL_CALLING_SIGNAL
    )
    assert (
        classifier.run(
            _event(
                source_type="rss",
                title="OpenAI provider API behavior changed",
                content="JSON mode and model API compatibility guidance changed.",
            ),
            profile,
        ).category
        is SignalCategory.PROVIDER_COMPATIBILITY_SIGNAL
    )


def test_evidence_agent_preserves_primary_url() -> None:
    event = _event()

    result = EvidenceAgent().run(event)

    assert result.evidence_urls == [event.url]
    assert result.confidence > 0.8


@pytest.mark.asyncio
async def test_supervisor_runs_structured_agent_chain(project_root) -> None:
    profile = {
        "tech_stack": ["Python"],
        "critical_modules": ["checkpoint"],
        "dependencies": ["langgraph"],
        "competitors": [],
        "focus_keywords": ["checkpoint", "persistence"],
        "ignore_keywords": [],
    }
    policy = load_signal_policy(project_root / "configs" / "signal_policy.yaml")
    trace = TraceRecorder()
    supervisor = SupervisorAgent(
        SignalToolExecutor(create_signal_tool_registry(), cwd=project_root),
        trace=trace,
    )

    assessments = await supervisor.assess_batch(
        [_event()],
        project_profile=profile,
        policy=policy,
    )

    assert len(assessments) == 1
    assert assessments[0].score_breakdown is not None
    assert assessments[0].affected_modules == ["checkpoint", "langgraph"]
    assert {step.agent for step in trace.steps if step.agent} >= {
        "ClassifierAgent",
        "EvidenceAgent",
        "ImpactAgent",
        "ActionAgent",
    }


@pytest.mark.asyncio
async def test_noise_category_is_always_ignored(project_root) -> None:
    policy = load_signal_policy(project_root / "configs" / "signal_policy.yaml")
    supervisor = SupervisorAgent(
        SignalToolExecutor(create_signal_tool_registry(), cwd=project_root)
    )
    assessments = await supervisor.assess_batch(
        [_event(title="Consumer giveaway")],
        project_profile={
            "dependencies": [],
            "competitors": [],
            "critical_modules": [],
            "focus_keywords": [],
            "ignore_keywords": ["giveaway"],
        },
        policy=policy,
    )

    assert assessments[0].category is SignalCategory.NOISE
    assert assessments[0].decision.value == "ignore"
