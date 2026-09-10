from __future__ import annotations

import asyncio
import json
from collections import Counter
from pathlib import Path

from typer.testing import CliRunner

from signal_harness.agent_integration.mode import RunMode
from signal_harness.capability_eval import (
    CapabilitySuite,
    TrajectoryExpectation,
    _case_score,
    evaluate_trajectory_suite,
    load_capability_suite,
    run_capability_comparison,
)
from signal_harness.cli import app
from signal_harness.providers.adapter import AgentCall
from signal_harness.providers.mock_provider import MockProvider
from signal_harness.signal.policy import load_signal_policy, load_yaml_mapping
from signal_harness.signal.schemas import (
    SignalAssessment,
    SignalCategory,
    SignalDecision,
    TraceStep,
)

runner = CliRunner()


def _suite(project_root: Path) -> CapabilitySuite:
    return load_capability_suite(
        project_root / "examples/signal_harness/capability_golden_v1.json"
    )


def test_capability_golden_v1_is_hard_multidimensional_set(project_root: Path) -> None:
    suite = _suite(project_root)
    grades = Counter(case.expected.relevance_grade for case in suite.cases)
    truth = Counter(case.truth_status for case in suite.cases)

    assert suite.dataset_role == "capability"
    assert len(suite.cases) == 32
    assert grades[0] == 10
    assert grades[3] == 10
    assert sum(case.expected.require_uncertainty for case in suite.cases) == 9
    assert truth["partially_verified"] >= 6
    assert len({case.expected.expected_category for case in suite.cases}) >= 12
    assert all(case.expected.must_include_facts for case in suite.cases)


def test_fair_baseline_shares_context_and_does_not_fake_perfect_quality(
    project_root: Path,
) -> None:
    suite = _suite(project_root)
    provider = MockProvider()
    summary = asyncio.run(
        run_capability_comparison(
            provider=provider,
            mode=RunMode.MOCK_AGENT,
            suite=suite,
            project_profile=load_yaml_mapping(project_root / "configs/project_profile.yaml"),
            policy=load_signal_policy(project_root / "configs/signal_policy.yaml"),
            trials=1,
        )
    )

    assert summary.comparison_valid is True
    assert summary.recommendation_state == "plumbing_only"
    assert len({item.shared_context_hash for item in summary.variants}) == 1
    single, split = summary.variants
    assert single.variant == "shared-evidence-single-agent"
    assert split.variant == "split-impact-action-narrative"
    assert summary.batch_size == 8
    assert single.llm_call_count == 4
    assert split.llm_call_count == 12
    assert len(single.trial_summaries) == 1
    assert len(split.trial_summaries) == 1
    assert single.fallback_rate == 0
    assert split.fallback_rate == 0
    # Capability data is supposed to expose weaknesses; unlike regression it should
    # not be curated until the scripted implementation reaches 1.0.
    assert single.passed is False
    assert split.passed is False
    assert single.hard_negative_accuracy < 1
    assert split.uncertainty_pass_rate < 1


def test_real_mode_invalid_provider_output_never_marks_matrix_ready(
    project_root: Path,
) -> None:
    suite = _suite(project_root)
    provider = MockProvider(
        invalid_agents={
            "SharedEvidenceSingleAgentBaseline",
            "ImpactAnalystAgent",
            "ActionPlannerAgent",
            "ProjectNarrativeAgent",
        }
    )
    summary = asyncio.run(
        run_capability_comparison(
            provider=provider,
            mode=RunMode.AGENT,
            suite=suite,
            project_profile=load_yaml_mapping(project_root / "configs/project_profile.yaml"),
            policy=load_signal_policy(project_root / "configs/signal_policy.yaml"),
            trials=3,
        )
    )

    assert summary.comparison_valid is False
    assert summary.recommendation_state == "invalid_provider_or_schema"
    assert all(item.fallback_rate == 1.0 for item in summary.variants)
    assert "Do not compare" in summary.recommendation_reason


def test_capability_repeated_trials_measure_decision_consistency(project_root: Path) -> None:
    suite = _suite(project_root)
    summary = asyncio.run(
        run_capability_comparison(
            provider=MockProvider(),
            mode=RunMode.MOCK_AGENT,
            suite=suite,
            project_profile=load_yaml_mapping(project_root / "configs/project_profile.yaml"),
            policy=load_signal_policy(project_root / "configs/signal_policy.yaml"),
            trials=3,
        )
    )

    single, split = summary.variants
    assert single.trials == split.trials == 3
    assert single.decision_consistency_rate == 1.0
    assert split.decision_consistency_rate == 1.0
    assert single.llm_call_count == 12
    assert split.llm_call_count == 36
    assert len(single.trial_summaries) == 3
    assert len(split.trial_summaries) == 3


def test_bilingual_surface_aliases_preserve_semantic_rubric(
    project_root: Path,
) -> None:
    case = _suite(project_root).cases[3]
    assessment = SignalAssessment(
        event_id=case.id,
        category=SignalCategory.DEPENDENCY_UPDATE,
        relevance_score=10,
        impact_score=10,
        confidence=0.9,
        decision=SignalDecision.IGNORE,
        what_changed_zh=(
            "HTTPX 的 CVE-2026-41001 不影响项目当前锁定的 0.29.3；"
            "该版本已经不在受影响范围，当前未受影响。"
        ),
        why_relevant_zh="SignalHarness 使用 HTTPX 作为 API 客户端，因此版本适用性需要核对。",
        action_items_zh=["核对当前锁定版本仍为 0.29.3。"],
    )
    score = _case_score(case, assessment)

    assert score.fact_coverage == 1.0
    assert score.project_specificity == 1.0
    assert score.action_coverage == 1.0
    assert score.forbidden_claims_pass is True

    unsafe = assessment.model_copy(
        update={"what_changed_zh": "SignalHarness 当前受影响，需要立刻处理。"}
    )
    assert _case_score(case, unsafe).forbidden_claims_pass is False


def test_trajectory_grader_checks_behavior_contract(project_root: Path) -> None:
    suite = _suite(project_root)
    case = suite.cases[0].model_copy(
        update={
            "expected": suite.cases[0].expected.model_copy(
                update={
                    "trajectory": TrajectoryExpectation(
                        required_agents=["ImpactAnalystAgent"],
                        forbidden_tools=["mutation_tool"],
                        max_tool_requests=1,
                        require_no_fallback=True,
                        require_schema_valid=True,
                    )
                }
            )
        }
    )
    one_case = suite.model_copy(update={"cases": [case]})
    trace = [
        TraceStep(
            step="llm_agent_call",
            status="success",
            agent="ImpactAnalystAgent",
            agent_name="ImpactAnalystAgent",
            input_event_id=case.id,
            input_count=1,
            output_count=1,
            duration_ms=3,
            schema_valid=True,
            tools_requested=["signal_memory"],
            tools_executed=["signal_memory"],
        )
    ]
    ok = evaluate_trajectory_suite(trace=trace, suite=one_case)
    assert ok.pass_rate == 1.0

    broken = [
        trace[0].model_copy(
            update={
                "fallback_used": True,
                "tools_requested": ["signal_memory", "mutation_tool"],
            }
        )
    ]
    failed = evaluate_trajectory_suite(trace=broken, suite=one_case)
    assert failed.pass_rate == 0.0
    assert "fallback_used" in failed.failures[case.id]
    assert "forbidden_tool:mutation_tool" in failed.failures[case.id]


def test_capability_eval_cli_writes_matrix(project_root: Path, tmp_path: Path) -> None:
    output = tmp_path / "capability"
    result = runner.invoke(
        app,
        [
            "capability-eval",
            "--mode",
            "mock-agent",
            "--cwd",
            str(project_root),
            "--output-dir",
            str(output),
        ],
    )

    assert result.exit_code == 0, result.output
    assert "state=plumbing_only" in result.output
    assert (output / "capability_eval_summary.json").exists()
    assert (output / "capability_eval_summary.md").exists()


def test_mock_capability_cli_never_loads_real_project_env(
    project_root: Path, tmp_path: Path, monkeypatch
) -> None:
    def fail_env_load(root: Path) -> None:
        raise AssertionError(f"mock Capability must not load real project env: {root}")

    monkeypatch.setattr("signal_harness.cli._load_project_env", fail_env_load)
    result = runner.invoke(
        app,
        [
            "capability-eval",
            "--mode",
            "mock-agent",
            "--cwd",
            str(project_root),
            "--output-dir",
            str(tmp_path / "capability"),
        ],
    )

    assert result.exit_code == 0, result.output
    assert "state=plumbing_only" in result.output


def test_capability_trial_checkpoints_resume_without_provider_calls(
    project_root: Path, tmp_path: Path
) -> None:
    suite = _suite(project_root)
    profile = load_yaml_mapping(project_root / "configs/project_profile.yaml")
    policy = load_signal_policy(project_root / "configs/signal_policy.yaml")
    checkpoint_dir = tmp_path / "checkpoints"
    first_progress: list[dict[str, object]] = []
    first_provider = MockProvider()
    first = asyncio.run(
        run_capability_comparison(
            provider=first_provider,
            mode=RunMode.MOCK_AGENT,
            suite=suite,
            project_profile=profile,
            policy=policy,
            trials=1,
            checkpoint_dir=checkpoint_dir,
            progress_callback=first_progress.append,
        )
    )

    assert first.comparison_valid is True
    assert len(first_provider.calls) == 16
    assert (checkpoint_dir / "single.trial-01.json").exists()
    assert (checkpoint_dir / "split.trial-01.json").exists()
    assert any(item.get("stage") == "batch_completed" for item in first_progress)

    resumed_progress: list[dict[str, object]] = []
    resumed_provider = MockProvider()
    resumed = asyncio.run(
        run_capability_comparison(
            provider=resumed_provider,
            mode=RunMode.MOCK_AGENT,
            suite=suite,
            project_profile=profile,
            policy=policy,
            trials=1,
            checkpoint_dir=checkpoint_dir,
            resume=True,
            progress_callback=resumed_progress.append,
        )
    )

    assert resumed.comparison_valid is True
    assert resumed_provider.calls == []
    assert [item.get("stage") for item in resumed_progress] == [
        "trial_reused",
        "trial_reused",
    ]

    changed_provider = MockProvider()
    changed_policy = {**policy, "_eval_signature_marker": "changed"}
    changed = asyncio.run(
        run_capability_comparison(
            provider=changed_provider,
            mode=RunMode.MOCK_AGENT,
            suite=suite,
            project_profile=profile,
            policy=changed_policy,
            trials=1,
            checkpoint_dir=checkpoint_dir,
            resume=True,
        )
    )
    assert changed.comparison_valid is True
    assert changed_provider.calls


def test_capability_coverage_diagnostics_name_missing_stage(project_root: Path) -> None:
    suite = _suite(project_root)
    provider = MockProvider(
        responses={
            "ProjectNarrativeOutput": '{"report_zh":"diagnostic","results":[]}'
        }
    )
    summary = asyncio.run(
        run_capability_comparison(
            provider=provider,
            mode=RunMode.MOCK_AGENT,
            suite=suite,
            project_profile=load_yaml_mapping(project_root / "configs/project_profile.yaml"),
            policy=load_signal_policy(project_root / "configs/signal_policy.yaml"),
            trials=1,
        )
    )

    split = next(
        item for item in summary.variants if item.variant == "split-impact-action-narrative"
    )
    assert split.coverage_valid_rate == 0.0
    assert split.trial_summaries[0].coverage_diagnostics
    assert any(
        "narrative missing=" in item
        for item in split.trial_summaries[0].coverage_diagnostics
    )


class _CapabilityNarrativeOmissionOnceProvider(MockProvider):
    def __init__(self) -> None:
        super().__init__()
        self._omitted_once = False

    async def complete(self, call: AgentCall) -> str:
        response = await super().complete(call)
        if call.output_schema == "ProjectNarrativeOutput" and not self._omitted_once:
            payload = json.loads(response)
            results = list(payload.get("results", []))
            if results:
                payload["results"] = results[:-1]
                self._omitted_once = True
                return json.dumps(payload, ensure_ascii=False)
        return response


def test_capability_semantic_coverage_repair_preserves_valid_matrix(
    project_root: Path,
) -> None:
    full = _suite(project_root)
    suite = full.model_copy(update={"suite": "coverage-repair", "cases": full.cases[:4]})
    provider = _CapabilityNarrativeOmissionOnceProvider()
    summary = asyncio.run(
        run_capability_comparison(
            provider=provider,
            mode=RunMode.MOCK_AGENT,
            suite=suite,
            project_profile=load_yaml_mapping(project_root / "configs/project_profile.yaml"),
            policy=load_signal_policy(project_root / "configs/signal_policy.yaml"),
            trials=1,
            batch_size=4,
        )
    )

    split = next(
        item for item in summary.variants if item.variant == "split-impact-action-narrative"
    )
    assert split.coverage_valid_rate == 1.0
    assert split.schema_valid_rate == 1.0
    assert split.fallback_rate == 0.0
    assert split.trial_summaries[0].coverage_diagnostics == []
    assert split.llm_call_count == 4


def test_capability_checkpoint_rejects_stale_experiment_signature(
    project_root: Path, tmp_path: Path
) -> None:
    suite = _suite(project_root)
    profile = load_yaml_mapping(project_root / "configs/project_profile.yaml")
    policy = load_signal_policy(project_root / "configs/signal_policy.yaml")
    checkpoint_dir = tmp_path / "checkpoints"
    first_provider = MockProvider()
    asyncio.run(
        run_capability_comparison(
            provider=first_provider,
            mode=RunMode.MOCK_AGENT,
            suite=suite,
            project_profile=profile,
            policy=policy,
            trials=1,
            checkpoint_dir=checkpoint_dir,
        )
    )
    single_path = checkpoint_dir / "single.trial-01.json"
    payload = json.loads(single_path.read_text(encoding="utf-8"))
    payload["experiment_signature"] = "stale-signature"
    single_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    resumed_progress: list[dict[str, object]] = []
    resumed_provider = MockProvider()
    asyncio.run(
        run_capability_comparison(
            provider=resumed_provider,
            mode=RunMode.MOCK_AGENT,
            suite=suite,
            project_profile=profile,
            policy=policy,
            trials=1,
            checkpoint_dir=checkpoint_dir,
            resume=True,
            progress_callback=resumed_progress.append,
        )
    )

    # Only the stale single-Agent trial reruns (4 mini-batches); split is reused.
    assert len(resumed_provider.calls) == 4
    assert any(
        item.get("stage") == "trial_started" and item.get("variant") == "single"
        for item in resumed_progress
    )
    assert any(
        item.get("stage") == "trial_reused" and item.get("variant") == "split"
        for item in resumed_progress
    )


def test_hard_negative_respects_acceptable_save_decision(project_root: Path) -> None:
    case = next(item for item in _suite(project_root).cases if item.id == "cap-004")
    assessment = SignalAssessment(
        event_id=case.id,
        category=SignalCategory(case.expected.expected_category),
        relevance_score=20,
        impact_score=50,
        confidence=0.9,
        decision=SignalDecision.SAVE,
        what_changed_zh="HTTPX 0.29.3 不在 CVE-2026-41001 的受影响范围。",
        why_relevant_zh="项目使用 HTTPX，因此需要保留版本适用性结论作为审计上下文。",
        action_items_zh=[],
    )
    score = _case_score(case, assessment)

    assert score.decision_pass is True
    assert score.hard_negative_pass is True


def test_uncertainty_grader_accepts_status_qualified_language(project_root: Path) -> None:
    case = next(item for item in _suite(project_root).cases if item.id == "cap-009")
    assessment = SignalAssessment(
        event_id=case.id,
        category=SignalCategory(case.expected.expected_category),
        relevance_score=60,
        impact_score=55,
        confidence=0.7,
        decision=SignalDecision.SAVE,
        what_changed_zh=(
            "MCP 正在讨论 outputSchema 提案，但该议题尚未合并，也未纳入正式规范。"
        ),
        why_relevant_zh="项目依赖 MCP 的结构化 Schema，因此需要持续跟踪而不是按已发布变更处理。",
        action_items_zh=["继续观察正式规范发布状态。"],
    )
    score = _case_score(case, assessment)

    assert score.uncertainty_pass is True


def test_capability_regrade_changes_scoring_without_provider_calls(
    project_root: Path, tmp_path: Path
) -> None:
    from copy import deepcopy

    from signal_harness.capability_eval import regrade_capability_checkpoints

    full = _suite(project_root)
    suite = full.model_copy(update={"suite": "scoring-regrade", "cases": full.cases[:8]})
    profile = load_yaml_mapping(project_root / "configs/project_profile.yaml")
    policy = load_signal_policy(project_root / "configs/signal_policy.yaml")
    checkpoint_dir = tmp_path / "checkpoints"
    provider = MockProvider()
    original = asyncio.run(
        run_capability_comparison(
            provider=provider,
            mode=RunMode.MOCK_AGENT,
            suite=suite,
            project_profile=profile,
            policy=policy,
            trials=1,
            batch_size=4,
            checkpoint_dir=checkpoint_dir,
        )
    )
    calls_after_generation = len(provider.calls)
    regrade_policy = deepcopy(policy)
    regrade_policy["thresholds"]["alert"] = 45
    regraded = regrade_capability_checkpoints(
        checkpoint_dir=checkpoint_dir,
        suite=suite,
        project_profile=profile,
        policy=regrade_policy,
        trials=1,
        batch_size=4,
    )

    assert len(provider.calls) == calls_after_generation
    assert regraded.evaluation_source == "checkpoint_regrade"
    assert regraded.scoring_version == "guarded-scoring-v2"
    assert regraded.comparison_valid is True
    original_decisions = {
        item.case_id: item.decision
        for variant in original.variants
        for item in variant.observed_outputs
    }
    regraded_decisions = {
        item.case_id: item.decision
        for variant in regraded.variants
        for item in variant.observed_outputs
    }
    assert original_decisions != regraded_decisions


def test_capability_observed_output_never_exposes_raw_permission_actions(
    project_root: Path,
) -> None:
    from signal_harness.capability_eval import _VariantTrial, _aggregate_variant, _trial_metrics

    full = _suite(project_root)
    case = full.cases[0]
    suite = full.model_copy(update={"suite": "presentation-safety", "cases": [case]})
    assessment = SignalAssessment(
        event_id=case.id,
        category=SignalCategory(case.expected.expected_category),
        relevance_score=95,
        impact_score=80,
        confidence=0.9,
        decision=SignalDecision.ALERT,
        what_changed_zh="Pydantic v3 修改了结构化输出校验行为。",
        why_relevant_zh="项目依赖 Pydantic 进行结构化输出验证。",
        action_items_zh=[],
        action_items=[
            "先运行结构化输出回归测试。",
            "Approval required before `检查 Pydantic v3 迁移路径`: "
            "检查 Pydantic v3 迁移路径 is not enabled",
            "Approval required before `monitor_internal_tool`: monitor_internal_tool is not enabled",
            "Human approval is required before execution.",
        ],
    )
    case_scores, metrics = _trial_metrics(assessments=[assessment], suite=suite)
    summary = _aggregate_variant(
        variant="shared-evidence-single-agent",
        shared_hash="shared",
        suite=suite,
        trials=[
            _VariantTrial(
                assessments=[assessment],
                trace=[],
                coverage_valid=True,
                coverage_diagnostics=[],
                case_scores=case_scores,
                metrics=metrics,
            )
        ],
    )

    actions = summary.observed_outputs[0].recommended_actions_zh
    assert actions == ["先运行结构化输出回归测试。", "检查 Pydantic v3 迁移路径"]
    assert all("Approval required before" not in item for item in actions)
    assert all("Human approval" not in item for item in actions)
