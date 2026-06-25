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
        _event(),
        {"dependencies": ["langgraph"], "ignore_keywords": []},
    )

    assert result.category is SignalCategory.DEPENDENCY_UPDATE


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
