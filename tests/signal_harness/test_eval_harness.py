from __future__ import annotations

import asyncio
from pathlib import Path

from openharness.tools.base import ToolResult
from signal_harness.agent_integration.context_builder import PromptContextBuilder
from signal_harness.agent_integration.mode import RunMode
from signal_harness.agent_integration.schemas import EvidenceToolPlan, ToolRequest
from signal_harness.evals import build_eval_summary
from signal_harness.providers.mock_provider import MockProvider
from signal_harness.runtime.cache import SourceFetchCache, ToolObservationCache
from signal_harness.runtime.workflow import SignalHarnessWorkflow
from signal_harness.runtime.permissions import SignalPermissionGuard
from signal_harness.signal.feedback import create_feedback_record
from signal_harness.signal.noise import NoiseFilter
from signal_harness.signal.normalizer import normalize_event
from signal_harness.signal.policy import load_signal_policy
from signal_harness.signal.schemas import SignalCategory
from signal_harness.ui.trace_view import write_trace_summary


def _fixture(project_root: Path) -> Path:
    return project_root / "examples/signal_harness/eval_multisource_events.json"


def _scan(
    project_root: Path,
    tmp_path: Path,
    provider: MockProvider | None = None,
):
    workflow = SignalHarnessWorkflow(
        cwd=project_root,
        output_dir=tmp_path / "outputs",
        state_dir=tmp_path / "state",
        mode=RunMode.MOCK_AGENT,
        provider=provider or MockProvider(strategy="scripted"),
    )
    return asyncio.run(workflow.scan(fixture=_fixture(project_root)))


def test_scripted_eval_routes_noise_and_multisource(
    project_root: Path,
    tmp_path: Path,
) -> None:
    result = _scan(project_root, tmp_path)
    by_id = {item.event_id: item for item in result.assessments}

    assert by_id["multi-001"].category is SignalCategory.DEPENDENCY_UPDATE
    assert by_id["multi-001"].decision.value == "action_required"
    assert by_id["multi-002"].decision.value == "alert"
    assert by_id["multi-003"].decision.value == "save"
    assert by_id["multi-001"].required_agents == [
        "context_evidence",
        "impact",
        "action",
        "learning_observation",
    ]
    assert by_id["multi-003"].required_agents == [
        "context_evidence",
        "impact",
        "learning_observation",
    ]
    assert by_id["multi-004"].category is SignalCategory.NOISE
    assert by_id["multi-004"].decision.value == "ignore"
    assert "low_information_web_change" in (by_id["multi-004"].noise_reason or "")
    assert set(by_id["multi-001"].related_event_ids) >= {
        "multi-002",
        "multi-003",
    }
    assert by_id["multi-001"].cross_source_confidence >= 0.8
    assert by_id["multi-005"].conflicting_evidence
    action_trace = next(
        step for step in result.trace.steps if step.output_schema == "ActionOutput"
    )
    assert action_trace.input_count == 2
    assert action_trace.output_count == 2


def test_evidence_tool_loop_executes_blocks_and_reprompts(
    project_root: Path,
    tmp_path: Path,
) -> None:
    plan = EvidenceToolPlan(
        source_types_observed=["github_release", "rss"],
        tool_requests=[
            ToolRequest(
                tool_name="signal_memory",
                arguments={"action": "load_project_profile"},
                reason="Read stable project context.",
            ),
            ToolRequest(
                tool_name="bash",
                arguments={"command": "echo unsafe"},
                reason="This request must be blocked.",
            ),
        ],
        planning_summary="Exercise allow and block behavior.",
    )
    provider = MockProvider(
        strategy="scripted",
        responses={"EvidenceToolPlan": plan.model_dump_json()},
    )
    result = _scan(project_root, tmp_path, provider)
    plan_trace = next(
        step
        for step in result.trace.steps
        if step.output_schema == "EvidenceToolPlan"
    )
    final_call = next(
        call
        for call in provider.calls
        if call.output_schema == "ContextEvidenceOutput"
    )

    assert "signal_memory" in plan_trace.tools_executed
    assert "bash" in plan_trace.blocked_tools
    assert any("bash:blocked" in item for item in plan_trace.permission_checks)
    observations = final_call.input_payload["tool_observations"]
    assert {item["status"] for item in observations} == {"success", "blocked"}


def test_scripted_multi_tool_eval_uses_mock_outputs_without_network(
    project_root: Path,
    tmp_path: Path,
) -> None:
    plan = EvidenceToolPlan(
        source_types_observed=["github_release", "rss", "web_change"],
        tool_requests=[
            ToolRequest(
                tool_name="github_signal",
                arguments={"mock_tool_eval": True},
                reason="Read a mocked GitHub primary-source observation.",
            ),
            ToolRequest(
                tool_name="rss_signal",
                arguments={"mock_tool_eval": True},
                reason="Read a mocked RSS commentary observation.",
            ),
            ToolRequest(
                tool_name="web_change",
                arguments={"mock_tool_eval": True},
                reason="Read a mocked web-change observation.",
            ),
            ToolRequest(
                tool_name="bash",
                arguments={"command": "echo blocked"},
                reason="Prove that a disallowed tool is blocked.",
            ),
            ToolRequest(
                tool_name="signal_score",
                arguments={"mock_tool_error": True},
                reason="Prove that one tool error does not crash the scan.",
            ),
        ],
        planning_summary="Exercise multiple allowed, blocked, and failed tools.",
    )
    provider = MockProvider(
        strategy="scripted",
        responses={"EvidenceToolPlan": plan.model_dump_json()},
    )
    workflow = SignalHarnessWorkflow(
        cwd=project_root,
        output_dir=tmp_path / "outputs",
        state_dir=tmp_path / "state",
        mode=RunMode.MOCK_AGENT,
        provider=provider,
    )
    original_call = workflow.executor.call
    mocked_calls: list[str] = []

    async def mock_tool_call(
        name: str,
        arguments: dict[str, object],
    ) -> ToolResult:
        if arguments.get("mock_tool_eval") is True:
            mocked_calls.append(name)
            return ToolResult(
                output=f'{{"tool": "{name}", "mocked": true}}'
            )
        if arguments.get("mock_tool_error") is True:
            return ToolResult(output="mock signal_score failure", is_error=True)
        return await original_call(name, arguments)

    workflow.executor.call = mock_tool_call  # type: ignore[method-assign]
    result = asyncio.run(workflow.scan(fixture=_fixture(project_root)))
    plan_trace = next(
        step
        for step in result.trace.steps
        if step.output_schema == "EvidenceToolPlan"
    )
    final_trace = next(
        step
        for step in result.trace.steps
        if step.output_schema == "ContextEvidenceOutput"
    )
    final_call = next(
        call
        for call in provider.calls
        if call.output_schema == "ContextEvidenceOutput"
    )

    assert mocked_calls == ["github_signal", "rss_signal", "web_change"]
    assert plan_trace.tools_requested == [
        "github_signal",
        "rss_signal",
        "web_change",
        "bash",
        "signal_score",
    ]
    assert plan_trace.tools_executed == [
        "github_signal",
        "rss_signal",
        "web_change",
    ]
    assert plan_trace.blocked_tools == ["bash"]
    assert any("mock signal_score failure" in item for item in plan_trace.tool_errors)
    assert len(plan_trace.permission_checks) == 5
    assert final_trace.event_input_count == 4
    assert final_trace.tool_observation_count == 5
    assert final_trace.input_count == 9
    assert final_trace.source_type_count == 3
    assert final_trace.tools_requested_count == 5
    assert final_trace.tools_executed_count == 3
    assert final_trace.exit_condition == "completed_with_tool_errors"
    observations = final_call.input_payload["tool_observations"]
    assert [item["status"] for item in observations] == [
        "success",
        "success",
        "success",
        "blocked",
        "error",
    ]
    assert result.assessments
    summary_path = write_trace_summary(
        tmp_path / "trace-output",
        result.trace.steps,
    )
    summary = summary_path.read_text(encoding="utf-8")
    assert "## ContextEvidenceAgent Final" in summary
    assert "- events: 4" in summary
    assert "- tool_observations: 5" in summary
    assert "- total_inputs: 9" in summary
    assert "- exit_condition: completed_with_tool_errors" in summary
    assert "## Skipped Event Audit Completion" in summary
    assert "not downstream LLM Agent execution" in summary


def test_tool_error_does_not_crash_and_caps_confidence(
    project_root: Path,
    tmp_path: Path,
) -> None:
    plan = EvidenceToolPlan(
        source_types_observed=["rss"],
        tool_requests=[
            ToolRequest(
                tool_name="signal_score",
                arguments={},
                reason="Create a validation error for eval.",
            )
        ],
    )
    result = _scan(
        project_root,
        tmp_path,
        MockProvider(
            strategy="scripted",
            responses={"EvidenceToolPlan": plan.model_dump_json()},
        ),
    )
    evidence_trace = next(
        step
        for step in result.trace.steps
        if step.output_schema == "ContextEvidenceOutput"
    )

    assert evidence_trace.tool_errors
    assert max(
        item.confidence
        for item in result.assessments
        if item.event_id != "multi-004"
    ) <= 0.55


def test_prompt_prefix_hash_stability_and_dynamic_change() -> None:
    builder = PromptContextBuilder()
    common = {
        "agent_name": "ContextEvidenceAgent",
        "role_instructions": "Verify evidence.",
        "output_schema": {"type": "object"},
        "project_context": {
            "project_profile": {"project_name": "SignalHarness"},
            "policy": {"version": 1},
        },
        "memory": {"signal_memory": {"seen_signals": []}},
    }
    first = builder.build(
        **common,
        dynamic_payload={"events": [{"event_id": "a"}]},
        volatile_metadata={"timestamp": "2026-06-26T01:00:00Z"},
    )
    second = builder.build(
        **common,
        dynamic_payload={"events": [{"event_id": "b"}]},
        volatile_metadata={"timestamp": "2026-06-26T02:00:00Z"},
    )

    assert first.prompt_prefix_hash == second.prompt_prefix_hash
    assert first.static_context_hash == second.static_context_hash
    assert first.dynamic_context_hash != second.dynamic_context_hash
    assert "timestamp" not in first.system_prompt()


def test_known_false_positive_pattern_is_downweighted(project_root: Path) -> None:
    policy = load_signal_policy(project_root / "configs/signal_policy.yaml")
    event = normalize_event(
        {
            "event_id": "noise-feedback",
            "source_type": "rss",
            "source_name": "Marketing",
            "title": "Promotional roundup",
            "content": "Weekly marketing roundup.",
            "url": "https://example.com/marketing",
            "collected_at": "2026-06-26T00:00:00Z",
        }
    )
    assessment = NoiseFilter().evaluate(
        [event],
        policy=policy,
        feedback_history=[
            create_feedback_record(
                "old",
                "false_positive",
                "marketing roundup",
            )
        ],
    )[0]

    assert assessment.score_multiplier < 1
    assert "known_false_positive_pattern" in assessment.matched_rules


def test_local_cache_primitives_are_lightweight(tmp_path: Path) -> None:
    source_cache = SourceFetchCache(tmp_path / "cache")
    key = source_cache.key("rss_signal", {"url": "https://example.com/feed"})
    source_cache.put(
        key=key,
        source_type="rss",
        source_name="Example",
        ttl_seconds=900,
        payload=[{"title": "cached"}],
    )
    entry = source_cache.get(key)
    run_cache = ToolObservationCache()
    tool_key = run_cache.key("signal_memory", {"action": "load_project_profile"})
    run_cache.put(tool_key, {"status": "success"})

    assert entry is not None
    assert entry.payload == [{"title": "cached"}]
    assert run_cache.get(tool_key) == {"status": "success"}


def test_identical_tool_requests_use_per_run_cache(
    project_root: Path,
    tmp_path: Path,
) -> None:
    request = ToolRequest(
        tool_name="signal_memory",
        arguments={"action": "load_project_profile"},
        reason="Repeated lookup for cache eval.",
    )
    plan = EvidenceToolPlan(
        source_types_observed=["github_release"],
        tool_requests=[request, request],
    )
    result = _scan(
        project_root,
        tmp_path,
        MockProvider(
            responses={"EvidenceToolPlan": plan.model_dump_json()},
        ),
    )
    trace = next(
        step for step in result.trace.steps if step.output_schema == "EvidenceToolPlan"
    )

    assert "tool_observation:signal_memory:miss" in trace.cache_events
    assert "tool_observation:signal_memory:hit" in trace.cache_events


def test_source_fetch_cache_is_reported_on_second_collection(
    project_root: Path,
    tmp_path: Path,
) -> None:
    workflow = SignalHarnessWorkflow(
        cwd=project_root,
        state_dir=tmp_path / "state",
    )
    calls = 0

    async def fake_call(name: str, arguments: dict[str, object]) -> ToolResult:
        nonlocal calls
        calls += 1
        return ToolResult(output='[{"name": "v1"}]')

    workflow.executor.call = fake_call  # type: ignore[method-assign]
    watchlist = {
        "github": {
            "repositories": [
                {"repo": "example/repo", "events": ["releases"]}
            ]
        }
    }
    policy = load_signal_policy(project_root / "configs/signal_policy.yaml")
    guard = SignalPermissionGuard(policy)
    first = asyncio.run(
        workflow._collect_watchlist(watchlist, since=None, guard=guard)
    )
    second = asyncio.run(
        workflow._collect_watchlist(watchlist, since=None, guard=guard)
    )

    assert first.source_tasks[0].cache_hit is False
    assert second.source_tasks[0].cache_hit is True
    assert calls == 1


def test_eval_summary_reports_workflow_metrics(
    project_root: Path,
    tmp_path: Path,
) -> None:
    result = _scan(project_root, tmp_path)
    summary = build_eval_summary(
        result.assessments,
        result.trace.steps,
        expected_categories={
            "multi-001": SignalCategory.DEPENDENCY_UPDATE,
            "multi-004": SignalCategory.NOISE,
        },
        expected_noise_ids={"multi-004"},
        proposal_safety_passed=True,
    )

    assert summary.route_accuracy == 1
    assert summary.noise_filter_accuracy == 1
    assert summary.evidence_primary_source_coverage == 1
    assert summary.fallback_rate == 0
    assert summary.proposal_safety_passed is True
