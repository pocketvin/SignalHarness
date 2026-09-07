from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from pathlib import Path

import pytest

from signal_harness.agent_integration.mode import RunMode
from signal_harness.persistence import ChangeLedger
from signal_harness.runtime.workflow import SignalHarnessWorkflow
from signal_harness.signal.normalizer import normalize_event
from signal_harness.signal.policy import load_signal_policy, load_yaml_mapping


def _raw_event(index: int, *, content: str | None = None) -> dict[str, object]:
    now = datetime(2026, 9, 8, 0, 0, tzinfo=timezone.utc)
    return {
        "event_id": f"event-{index:03d}",
        "source_type": "github_issue",
        "source_name": "example/project",
        "title": f"Checkpoint persistence change {index:03d}",
        "content": content or f"Durability update {index:03d}",
        "url": f"https://example.test/issues/{index}",
        "published_at": now.isoformat(),
        "collected_at": now.isoformat(),
        "raw_payload": {"id": index, "source_authority": "maintainer"},
    }


def test_event_revision_preserves_history_under_one_change(tmp_path: Path) -> None:
    ledger = ChangeLedger(tmp_path / "ledger.sqlite3")
    first = normalize_event(_raw_event(1, content="Version one"))
    second = normalize_event(_raw_event(1, content="Version two"))

    first_map = ledger.persist_observations([first])
    second_map = ledger.persist_observations([second])

    assert first_map[first.event_id][0] == second_map[second.event_id][0]
    assert ledger.revision_count(event_id=first.event_id) == 2


def test_scan_projection_stays_on_original_event_revision(
    project_root: Path,
    tmp_path: Path,
) -> None:
    ledger = ChangeLedger(tmp_path / "ledger.sqlite3")
    profile = load_yaml_mapping(project_root / "configs/project_profile.yaml")
    policy = load_signal_policy(project_root / "configs/signal_policy.yaml")
    first = normalize_event(_raw_event(7, content="Version one"))
    second = normalize_event(_raw_event(7, content="Version two"))

    ledger.begin_scan(scan_id="scan-one", project_id="signalharness", collected_count=1, deduped_count=1)
    first_refs = ledger.persist_observations([first])
    ledger.freeze_scan_changes(
        scan_id="scan-one",
        project_id="signalharness",
        events=[first],
        event_change_ids=first_refs,
        project_profile=profile,
        policy=policy,
        analyzed_event_ids=set(),
    )
    ledger.complete_scan(scan_id="scan-one", analyzed_count=0, relevant_count=1)

    ledger.begin_scan(scan_id="scan-two", project_id="signalharness", collected_count=1, deduped_count=1)
    second_refs = ledger.persist_observations([second])
    ledger.freeze_scan_changes(
        scan_id="scan-two",
        project_id="signalharness",
        events=[second],
        event_change_ids=second_refs,
        project_profile=profile,
        policy=policy,
        analyzed_event_ids=set(),
    )
    ledger.complete_scan(scan_id="scan-two", analyzed_count=0, relevant_count=1)

    old_item = ledger.list_scan_changes("scan-one", limit=10).items[0]
    new_item = ledger.list_scan_changes("scan-two", limit=10).items[0]
    assert old_item["event"]["content"] == "Version one"
    assert new_item["event"]["content"] == "Version two"
    assert old_item["change_id"] == new_item["change_id"]


def test_workflow_keeps_all_pre_funnel_changes_queryable(
    project_root: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workflow = SignalHarnessWorkflow(
        cwd=project_root,
        output_dir=tmp_path / "outputs",
        state_dir=tmp_path / "state",
        mode=RunMode.DEMO,
        project_id="signalharness",
    )
    fixture_events = [_raw_event(index) for index in range(320)]

    async def fake_fixture(_fixture: object) -> list[dict[str, object]]:
        return fixture_events

    monkeypatch.setattr(workflow, "_load_fixture", fake_fixture)
    result = asyncio.run(workflow.scan(fixture="fixture.json", max_events=12))

    assert len(result.signals) == 12
    assert result.all_change_count == 320
    first_page = workflow.ledger.list_scan_changes(result.scan_id, offset=0, limit=25)
    last_page = workflow.ledger.list_scan_changes(result.scan_id, offset=295, limit=25)
    assert first_page.count == 320
    assert len(first_page.items) == 25
    assert first_page.has_more is True
    assert len(last_page.items) == 25
    assert last_page.has_more is False

    all_items = workflow.ledger.list_scan_changes(result.scan_id, offset=0, limit=400).items
    assert sum(1 for item in all_items if item["selected_for_analysis"]) == 12
    assert sum(1 for item in all_items if item["assessment"] is not None) == 12


def test_report_failure_preserves_ledger_and_does_not_consume_seen_memory(
    project_root: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state_dir = tmp_path / "state"
    workflow = SignalHarnessWorkflow(
        cwd=project_root,
        output_dir=tmp_path / "outputs",
        state_dir=state_dir,
        mode=RunMode.DEMO,
        project_id="signalharness",
    )

    async def fail_outputs(*args: object, **kwargs: object) -> None:
        del args, kwargs
        raise RuntimeError("forced report failure")

    monkeypatch.setattr(workflow, "_write_outputs", fail_outputs)
    fixture = project_root / "examples" / "signal_harness" / "sample_events.json"
    with pytest.raises(RuntimeError, match="forced report failure"):
        asyncio.run(workflow.scan(fixture=fixture, max_events=2, scan_id="failed-scan"))

    page = workflow.ledger.list_scan_changes("failed-scan", limit=100)
    assert page.count == 4
    assert not (state_dir / "signal_memory.json").exists()
