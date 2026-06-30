from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
from pydantic import ValidationError
from typer.testing import CliRunner

from signal_harness.agent_integration.mode import RunMode
from signal_harness.agent_integration.schemas import (
    ActionItem,
    ActionOutput,
    ImpactItem,
    ImpactOutput,
    SupervisorOutput,
)
from signal_harness.agent_team.signal_supervisor import SignalSupervisorAgent
from signal_harness.cli import app
from signal_harness.memory import (
    FeedbackMemory,
    PolicyMemory,
    ProjectMemory,
    SignalMemory,
)
from signal_harness.memory.replay import evaluate_policy_replay
from signal_harness.providers.adapter import AgentCall
from signal_harness.providers.mock_provider import MockProvider
from signal_harness.runtime.workflow import SignalHarnessWorkflow
from signal_harness.signal.feedback import create_feedback_record
from signal_harness.signal.normalizer import normalize_event
from signal_harness.signal.policy import load_signal_policy
from signal_harness.signal.schemas import SignalCategory

runner = CliRunner()
AGENT_NAMES = {
    "SignalSupervisorAgent",
    "ContextEvidenceAgent",
    "ImpactAnalystAgent",
    "ActionPlannerAgent",
    "LearningPolicyAgent",
}


def _fixture(project_root: Path) -> Path:
    return project_root / "examples/signal_harness/sample_events.json"


def _workflow(
    project_root: Path,
    tmp_path: Path,
    provider: MockProvider,
) -> SignalHarnessWorkflow:
    return SignalHarnessWorkflow(
        cwd=project_root,
        output_dir=tmp_path / "outputs",
        state_dir=tmp_path / "state",
        mode=RunMode.MOCK_AGENT,
        provider=provider,
    )


def test_demo_mode_needs_no_llm_key(
    project_root: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    result = runner.invoke(
        app,
        [
            "scan",
            "--fixture",
            str(_fixture(project_root)),
            "--mode",
            "demo",
            "--cwd",
            str(project_root),
            "--output-dir",
            str(tmp_path / "outputs"),
            "--state-dir",
            str(tmp_path / "state"),
        ],
    )

    assert result.exit_code == 0, result.output


def test_agent_mode_requires_clear_key_error(
    project_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    result = runner.invoke(
        app,
        [
            "scan",
            "--fixture",
            str(_fixture(project_root)),
            "--mode",
            "agent",
            "--cwd",
            str(project_root),
        ],
    )

    assert result.exit_code != 0
    assert (
        "agent mode requires LLM_API_KEY. "
        "Use --mode demo or --mode mock-agent for offline execution."
    ) in result.output


def test_mock_agent_runs_complete_five_agent_team(
    project_root: Path,
    tmp_path: Path,
) -> None:
    provider = MockProvider()
    result = asyncio.run(
        _workflow(project_root, tmp_path, provider).scan(
            fixture=_fixture(project_root)
        )
    )

    assert {call.agent_name for call in provider.calls} == AGENT_NAMES
    assert [
        call.output_schema
        for call in provider.calls
        if call.agent_name == "ContextEvidenceAgent"
    ] == ["EvidenceToolPlan", "ContextEvidenceOutput"]
    llm_steps = [step for step in result.trace.steps if step.step == "llm_agent_call"]
    assert {step.agent_name for step in llm_steps} == AGENT_NAMES
    assert all(step.mode == "mock-agent" for step in llm_steps)
    assert all(step.output_schema for step in llm_steps)
    evidence_step = next(
        step for step in llm_steps if step.output_schema == "EvidenceToolPlan"
    )
    assert {
        "signal_memory",
        "github_signal",
        "rss_signal",
        "web_change",
    } <= set(evidence_step.tools_requested)
    assert {
        "signal_memory",
        "github_signal",
        "rss_signal",
        "web_change",
    } <= set(evidence_step.tools_executed)
    assert {"github_release", "github_issue", "rss", "web_change"}.issubset(
        evidence_step.source_types_observed
    )
    assert all(step.fallback_used is False for step in llm_steps)
    assert all(step.input_count != 1 for step in llm_steps)
    assert all(step.output_count is not None for step in llm_steps)
    assert all(step.prompt_prefix_hash for step in llm_steps)
    assert all(step.static_context_hash for step in llm_steps)
    assert all(step.dynamic_context_hash for step in llm_steps)
    assert (tmp_path / "outputs/radar_digest.md").exists()


def test_scripted_mock_does_not_call_agent_fallback(
    project_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events = json.loads(_fixture(project_root).read_text(encoding="utf-8"))

    def fail_fallback(*args, **kwargs):
        raise AssertionError("scripted provider must not call Agent fallback")

    monkeypatch.setattr(SignalSupervisorAgent, "fallback", fail_fallback)
    provider = MockProvider(strategy="scripted")
    response = asyncio.run(
        provider.complete(
            AgentCall(
                agent_name="SignalSupervisorAgent",
                system_prompt="system",
                user_prompt="user",
                prompt_version="v1",
                output_schema="SupervisorOutput",
                input_payload={
                    "events": events,
                    "noise_assessments": [],
                    "clusters": [],
                },
                input_count=len(events),
            )
        )
    )

    output = SupervisorOutput.model_validate_json(response)
    assert len(output.routes) == len(events)
    assert "Scripted mock" in output.batch_summary


def test_invalid_mock_json_uses_deterministic_fallback(
    project_root: Path,
    tmp_path: Path,
) -> None:
    provider = MockProvider(invalid_agents={"ContextEvidenceAgent"})
    result = asyncio.run(
        _workflow(project_root, tmp_path, provider).scan(
            fixture=_fixture(project_root)
        )
    )

    evidence_traces = [
        step
        for step in result.trace.steps
        if step.agent_name == "ContextEvidenceAgent"
    ]
    assert any(step.schema_valid is False for step in evidence_traces)
    assert any(step.fallback_used is True for step in evidence_traces)
    assert result.assessments


def test_mock_agent_can_follow_demo_with_duplicate_downweight_memory(
    project_root: Path,
    tmp_path: Path,
) -> None:
    state_dir = tmp_path / "state"
    fixture = _fixture(project_root)
    asyncio.run(
        SignalHarnessWorkflow(
            cwd=project_root,
            output_dir=tmp_path / "demo-output",
            state_dir=state_dir,
            mode=RunMode.DEMO,
        ).scan(fixture=fixture)
    )
    result = asyncio.run(
        SignalHarnessWorkflow(
            cwd=project_root,
            output_dir=tmp_path / "mock-output",
            state_dir=state_dir,
            mode=RunMode.MOCK_AGENT,
            provider=MockProvider(),
        ).scan(fixture=fixture)
    )

    assert result.assessments
    assert any(
        "explicit override" in assessment.reason.lower()
        for assessment in result.assessments
        if assessment.category is not SignalCategory.NOISE
    )


def test_impact_schema_rejects_final_score() -> None:
    with pytest.raises(ValidationError):
        ImpactItem.model_validate(
            {
                "event_id": "demo-001",
                "affected_modules": [],
                "semantic_relevance": 50,
                "risk_level": "medium",
                "impact_reason": "test",
                "final_score": 100,
            }
        )


def test_final_score_is_guarded_by_deterministic_scorer(
    project_root: Path,
    tmp_path: Path,
) -> None:
    events = json.loads(_fixture(project_root).read_text(encoding="utf-8"))
    impact = ImpactOutput(
        results=[
            ImpactItem(
                event_id=item["event_id"],
                affected_modules=["project-wide"],
                semantic_relevance=100,
                risk_level="critical",
                impact_reason="Mock semantic maximum.",
            )
            for item in events
            if item["event_id"] != "demo-004"
        ]
    )
    provider = MockProvider(
        responses={"ImpactAnalystAgent": impact.model_dump_json()}
    )
    result = asyncio.run(
        _workflow(project_root, tmp_path, provider).scan(
            fixture=_fixture(project_root)
        )
    )

    assessment = result.assessments[0]
    assert assessment.score_breakdown is not None
    assert assessment.agent_score_breakdown is not None
    assert assessment.impact_score == assessment.agent_score_breakdown.final_score
    assert assessment.impact_score < 100
    assert assessment.agent_score_breakdown.deterministic_weight >= 0.7


def test_permission_guard_blocks_llm_requested_high_risk_action(
    project_root: Path,
    tmp_path: Path,
) -> None:
    events = json.loads(_fixture(project_root).read_text(encoding="utf-8"))
    actions = ActionOutput(
        results=[
            ActionItem(
                event_id=item["event_id"],
                action_items=["Open a tracking issue automatically."],
                critic_notes="The model claims no approval is needed.",
                approval_required=False,
                requested_actions=["create_github_issue"],
            )
            for item in events
            if item["event_id"] in {"demo-001", "demo-002", "demo-004"}
        ]
    )
    provider = MockProvider(
        responses={"ActionPlannerAgent": actions.model_dump_json()}
    )
    result = asyncio.run(
        _workflow(project_root, tmp_path, provider).scan(
            fixture=_fixture(project_root)
        )
    )

    action_trace = next(
        step for step in result.trace.steps if step.agent_name == "ActionPlannerAgent"
    )
    assert any("create_github_issue:blocked" in item for item in action_trace.permission_checks)
    assert any(
        "Approval required before `create_github_issue`" in action
        for assessment in result.assessments
        for action in assessment.action_items
    )


def test_calibrate_mock_agent_writes_review_only_artifacts(
    project_root: Path,
    tmp_path: Path,
) -> None:
    output_dir = tmp_path / "outputs"
    state_dir = tmp_path / "state"
    scan_result = runner.invoke(
        app,
        [
            "scan",
            "--fixture",
            str(_fixture(project_root)),
            "--mode",
            "demo",
            "--cwd",
            str(project_root),
            "--output-dir",
            str(output_dir),
            "--state-dir",
            str(state_dir),
        ],
    )
    assert scan_result.exit_code == 0, scan_result.output
    original_policy = (project_root / "configs/signal_policy.yaml").read_text(
        encoding="utf-8"
    )
    original_watchlist = (project_root / "configs/watchlist.yaml").read_text(
        encoding="utf-8"
    )
    skill_path = project_root / "src/signal_harness/skills/signal_triage/SKILL.md"
    original_skill = skill_path.read_text(encoding="utf-8")

    result = runner.invoke(
        app,
        [
            "calibrate",
            "--mode",
            "mock-agent",
            "--cwd",
            str(project_root),
            "--output-dir",
            str(output_dir),
            "--state-dir",
            str(state_dir),
        ],
    )

    assert result.exit_code == 0, result.output
    for name in (
        "policy_update_proposal.json",
        "skill_update_proposal.md",
        "watchlist_update_proposal.json",
        "replay_evaluation.json",
    ):
        assert (state_dir / name).exists()
    for name in (
        "latest_policy_update_proposal.json",
        "latest_skill_update_proposal.md",
        "latest_watchlist_update_proposal.json",
        "latest_replay_evaluation.json",
    ):
        assert (output_dir / name).exists()
    assert (
        project_root / "configs/signal_policy.yaml"
    ).read_text(encoding="utf-8") == original_policy
    assert (
        project_root / "configs/watchlist.yaml"
    ).read_text(encoding="utf-8") == original_watchlist
    assert skill_path.read_text(encoding="utf-8") == original_skill


def test_replay_evaluation_compares_old_and_proposed_policy(
    project_root: Path,
) -> None:
    policy = load_signal_policy(project_root / "configs/signal_policy.yaml")
    proposed = {
        **policy,
        "thresholds": {**policy["thresholds"], "save": 90, "alert": 92},
    }
    event = normalize_event(
        {
            "event_id": "replay-1",
            "source_type": "rss",
            "source_name": "Expert",
            "title": "Checkpoint persistence",
            "collected_at": "2026-06-25T00:00:00Z",
        }
    )
    replay = evaluate_policy_replay(
        [event],
        [create_feedback_record("replay-1", "useful")],
        project_profile={"focus_keywords": ["checkpoint"], "ignore_keywords": []},
        old_policy=policy,
        proposed_policy=proposed,
    )

    assert 0 <= replay.old_precision_proxy <= 1
    assert 0 <= replay.new_precision_proxy <= 1
    assert replay.recommendation in {"review-and-consider", "reject-or-revise"}


def test_memory_is_named_as_infrastructure() -> None:
    assert {item.__name__ for item in (
        ProjectMemory,
        SignalMemory,
        FeedbackMemory,
        PolicyMemory,
    )} == {
        "ProjectMemory",
        "SignalMemory",
        "FeedbackMemory",
        "PolicyMemory",
    }


def test_no_openharness_provider_dependency(project_root: Path) -> None:
    assert not (project_root / "src/signal_harness/providers/openharness_provider.py").exists()
    assert not (project_root / "src/signal_harness/runtime/agent_loop.py").exists()
