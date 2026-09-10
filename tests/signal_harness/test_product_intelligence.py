from __future__ import annotations

import asyncio
from pathlib import Path

from signal_harness.agent_integration.mode import RunMode
from signal_harness.product_intelligence import ProductIntelligenceService
from signal_harness.runtime.workflow import ScanResult, SignalHarnessWorkflow


def _scan(
    project_root: Path, tmp_path: Path, *, max_events: int = 2
) -> tuple[ScanResult, ProductIntelligenceService]:
    workflow = SignalHarnessWorkflow(
        cwd=project_root,
        output_dir=tmp_path / "outputs",
        state_dir=tmp_path / "state",
        mode=RunMode.DEMO,
        project_id="signalharness",
    )
    fixture = project_root / "examples/signal_harness/sample_events.json"
    result = asyncio.run(workflow.scan(fixture=fixture, max_events=max_events))
    service = ProductIntelligenceService(
        ledger=workflow.ledger,
        project_id="signalharness",
    )
    return result, service


def test_product_projection_keeps_all_separate_from_top(
    project_root: Path,
    tmp_path: Path,
) -> None:
    result, service = _scan(project_root, tmp_path, max_events=2)

    small = service.projection(result.scan_id, top_count=1, all_limit=2)
    large = service.projection(result.scan_id, top_count=3, all_limit=2)

    assert small.scan_id == result.scan_id
    assert small.report.stats["all_change_count"] == 4
    assert small.report.stats["analyzed_count"] == 2
    assert small.all_changes.all_count == 4
    assert small.all_changes.returned == 2
    assert len(small.top_changes) == 1
    assert large.report.stats["all_change_count"] == 4
    assert large.all_changes.all_count == 4
    assert len(large.top_changes) >= len(small.top_changes)


def test_product_change_list_is_filterable_searchable_and_stable(
    project_root: Path,
    tmp_path: Path,
) -> None:
    result, service = _scan(project_root, tmp_path, max_events=2)

    first = service.list_changes(result.scan_id, offset=0, limit=2)
    second = service.list_changes(result.scan_id, offset=2, limit=2)
    searched = service.list_changes(result.scan_id, query="checkpoint", limit=10)
    analyzed = service.list_changes(result.scan_id, analyzed=True, limit=10)
    unanalyzed = service.list_changes(result.scan_id, analyzed=False, limit=10)
    by_score = service.list_changes(result.scan_id, sort="score", limit=10)

    assert first.count == second.count == 4
    assert first.has_more is True
    assert second.has_more is False
    assert {item.change_id for item in first.items}.isdisjoint(
        {item.change_id for item in second.items}
    )
    assert searched.count >= 1
    assert analyzed.count == 2
    assert unanalyzed.count == 2
    assert by_score.all_count == 4
    assert by_score.items[0].impact_score is not None or by_score.items[0].rank >= 1


def test_product_detail_and_markdown_are_product_facing(
    project_root: Path,
    tmp_path: Path,
) -> None:
    result, service = _scan(project_root, tmp_path, max_events=3)
    analyzed = service.list_changes(result.scan_id, analyzed=True, limit=10)
    detail = service.change_detail(analyzed.items[0].change_id, scan_id=result.scan_id)

    assert detail.change_id == analyzed.items[0].change_id
    assert detail.event_id
    assert detail.event_revision_id > 0
    assert detail.what_changed_zh
    assert detail.why_relevant_zh
    assert "rank" in detail.audit

    report_md = service.render_markdown(result.scan_id, mode="report", top_count=2)
    top_md = service.render_markdown(result.scan_id, mode="top", top_count=2)
    change_md = service.render_markdown(
        result.scan_id,
        mode="change",
        change_id=detail.change_id,
    )
    for rendered in (report_md, top_md, change_md):
        assert "llm_agent_call" not in rendered
        assert "Task Trace" not in rendered
    assert "完整变化" in report_md
    assert "Change ID" in change_md


def test_product_service_resolves_latest_successful_scan(
    project_root: Path,
    tmp_path: Path,
) -> None:
    first, service = _scan(project_root, tmp_path, max_events=1)
    workflow = SignalHarnessWorkflow(
        cwd=project_root,
        output_dir=tmp_path / "outputs-two",
        state_dir=tmp_path / "state",
        mode=RunMode.DEMO,
        project_id="signalharness",
    )
    fixture = project_root / "examples/signal_harness/sample_events.json"
    second = asyncio.run(workflow.scan(fixture=fixture, max_events=2))

    assert first.scan_id != second.scan_id
    assert service.resolve_scan_id() == second.scan_id
    assert service.report().scan_id == second.scan_id


def test_product_changes_expose_stable_impact_boards(
    project_root: Path,
    tmp_path: Path,
) -> None:
    result, service = _scan(project_root, tmp_path, max_events=4)

    page = service.list_changes(result.scan_id, limit=20)
    counts = service.report(result.scan_id).stats["impact_group_counts"]

    assert sum(counts.values()) == page.all_count == 4
    assert {item.impact_group for item in page.items}.issubset(
        {
            "project_code",
            "dependency_version",
            "security",
            "api_protocol",
            "upstream_issue",
            "tech_news",
            "other",
        }
    )
    assert "dependency_version" in {item.impact_group for item in page.items}
    assert "upstream_issue" in {item.impact_group for item in page.items}
    dependency = service.list_changes(
        result.scan_id,
        impact_group="dependency_version",
        limit=20,
    )
    assert dependency.count >= 1
    assert all(item.impact_group == "dependency_version" for item in dependency.items)


def test_mock_agent_product_copy_is_human_facing_chinese(
    project_root: Path,
    tmp_path: Path,
) -> None:
    workflow = SignalHarnessWorkflow(
        cwd=project_root,
        output_dir=tmp_path / "human-outputs",
        state_dir=tmp_path / "human-state",
        mode=RunMode.MOCK_AGENT,
        project_id="signalharness",
    )
    result = asyncio.run(
        workflow.scan(
            fixture=project_root / "examples/signal_harness/sample_events.json",
            max_events=3,
        )
    )
    service = ProductIntelligenceService(
        ledger=workflow.ledger,
        project_id="signalharness",
    )
    product = service.projection(result.scan_id, top_count=2, all_limit=10)

    assert "信息数量" in product.report.summary_zh
    assert "本次扫描冻结了" not in product.report.summary_zh
    first = product.top_changes[0]
    assert first.what_changed_zh
    assert first.why_relevant_zh
    assert first.recommended_actions_zh
    assert "影响分" not in first.why_relevant_zh
    assert all("Review " not in action for action in first.recommended_actions_zh)
    assert all("Approval required before" not in action for action in first.recommended_actions_zh)
    assert all("Human approval" not in action for action in first.recommended_actions_zh)
    assert all(" is not enabled" not in action for action in first.recommended_actions_zh)
    assert any(
        step.agent_name == "ProjectNarrativeAgent"
        for step in result.trace.steps
        if step.step == "llm_agent_call"
    )
