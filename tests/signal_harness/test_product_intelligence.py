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
