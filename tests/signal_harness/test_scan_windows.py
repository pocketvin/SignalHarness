from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from signal_harness.agent_integration.mode import RunMode
from signal_harness.persistence import ChangeLedger
from signal_harness.runtime.windows import resolve_scan_window
from signal_harness.runtime.workflow import CollectionBatch, SignalHarnessWorkflow
from signal_harness.signal.schemas import SourceTask


def test_since_last_first_use_defaults_to_seven_days(tmp_path: Path) -> None:
    ledger = ChangeLedger(tmp_path / "ledger.sqlite3")
    now = datetime(2026, 9, 8, 2, 0, tzinfo=timezone.utc)

    window = resolve_scan_window(
        ledger=ledger,
        project_id="demo",
        mode="since_last",
        now=now,
    )

    assert window.first_use is True
    assert window.lower == now - timedelta(days=7)
    assert window.upper == now
    assert window.checkpoint_eligible is True


def test_since_last_uses_persisted_interactive_checkpoint(tmp_path: Path) -> None:
    ledger = ChangeLedger(tmp_path / "ledger.sqlite3")
    checkpoint = datetime(2026, 9, 7, 12, 0, tzinfo=timezone.utc)
    ledger.advance_interactive_checkpoint(
        project_id="demo",
        checkpoint_at=checkpoint,
        scan_id="scan-one",
    )
    now = datetime(2026, 9, 8, 2, 0, tzinfo=timezone.utc)

    window = resolve_scan_window(
        ledger=ledger,
        project_id="demo",
        mode="since_last",
        now=now,
    )

    assert window.first_use is False
    assert window.lower == checkpoint
    assert window.upper == now


def test_custom_historical_window_does_not_advance_checkpoint(tmp_path: Path) -> None:
    ledger = ChangeLedger(tmp_path / "ledger.sqlite3")
    now = datetime(2026, 9, 8, 2, 0, tzinfo=timezone.utc)
    window = resolve_scan_window(
        ledger=ledger,
        project_id="demo",
        mode="custom",
        custom_from=datetime(2026, 8, 1, tzinfo=timezone.utc),
        custom_to=datetime(2026, 8, 7, tzinfo=timezone.utc),
        now=now,
    )
    assert window.checkpoint_eligible is False


def _event(event_id: str, published_at: datetime) -> dict[str, object]:
    return {
        "event_id": event_id,
        "source_type": "github_issue",
        "source_name": "example/project",
        "title": f"Change {event_id}",
        "content": "checkpoint persistence update",
        "url": f"https://example.test/{event_id}",
        "published_at": published_at.isoformat(),
        "collected_at": published_at.isoformat(),
        "raw_payload": {"id": event_id, "source_authority": "maintainer"},
    }


@pytest.mark.asyncio
async def test_live_since_last_success_advances_checkpoint(
    project_root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workflow = SignalHarnessWorkflow(
        cwd=project_root,
        output_dir=tmp_path / "out",
        state_dir=tmp_path / "state",
        mode=RunMode.DEMO,
        project_id="signalharness",
    )
    observed = datetime.now(timezone.utc) - timedelta(hours=1)
    task = SourceTask(
        task_id="source-one",
        source_name="example/project",
        source_type="github_issue",
        status="success",
        coverage_status="complete",
        pages_fetched=1,
        output_count=1,
    )

    async def fake_collect(*args: object, **kwargs: object) -> CollectionBatch:
        del args, kwargs
        return CollectionBatch(
            events=[_event("one", observed)],
            failed_sources=[],
            source_tasks=[task],
        )

    monkeypatch.setattr(workflow, "_collect_watchlist", fake_collect)
    result = await workflow.scan(window_mode="since_last")
    checkpoint = workflow.ledger.get_interactive_checkpoint(project_id="signalharness")

    assert result.window.mode == "since_last"
    assert result.window.first_use is True
    assert checkpoint == result.window.upper


@pytest.mark.asyncio
async def test_partial_live_scan_does_not_advance_checkpoint(
    project_root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workflow = SignalHarnessWorkflow(
        cwd=project_root,
        output_dir=tmp_path / "out",
        state_dir=tmp_path / "state",
        mode=RunMode.DEMO,
        project_id="signalharness",
    )
    observed = datetime.now(timezone.utc) - timedelta(hours=1)
    task = SourceTask(
        task_id="source-one",
        source_name="example/project",
        source_type="github_issue",
        status="success",
        coverage_status="partial",
        history_limited=True,
        pages_fetched=20,
        output_count=1,
    )

    async def fake_collect(*args: object, **kwargs: object) -> CollectionBatch:
        del args, kwargs
        return CollectionBatch(
            events=[_event("one", observed)],
            failed_sources=[],
            source_tasks=[task],
        )

    monkeypatch.setattr(workflow, "_collect_watchlist", fake_collect)
    result = await workflow.scan(window_mode="since_last")

    assert result.window.checkpoint_eligible is True
    assert workflow.ledger.get_interactive_checkpoint(project_id="signalharness") is None


def test_window_upper_bound_is_exclusive(project_root: Path, tmp_path: Path) -> None:
    workflow = SignalHarnessWorkflow(
        cwd=project_root,
        output_dir=tmp_path / "out",
        state_dir=tmp_path / "state",
        mode=RunMode.DEMO,
    )
    upper = datetime(2026, 9, 8, 2, 0, tzinfo=timezone.utc)
    before = workflow._normalize_collected(_event("before", upper - timedelta(seconds=1)))
    at_upper = workflow._normalize_collected(_event("at-upper", upper))

    assert workflow._is_within_window(before, upper - timedelta(days=1), upper) is True
    assert workflow._is_within_window(at_upper, upper - timedelta(days=1), upper) is False


@pytest.mark.asyncio
async def test_since_last_includes_late_discovery_and_revision_once(
    project_root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workflow = SignalHarnessWorkflow(
        cwd=project_root,
        output_dir=tmp_path / "out",
        state_dir=tmp_path / "state",
        mode=RunMode.DEMO,
        project_id="signalharness",
    )
    now = datetime.now(timezone.utc)
    workflow.ledger.advance_interactive_checkpoint(
        project_id="signalharness",
        checkpoint_at=now - timedelta(days=1),
        scan_id="prior-scan",
    )
    old_published = now - timedelta(days=3)
    version = {"content": "first observation"}
    task = SourceTask(
        task_id="source-one",
        source_name="example/project",
        source_type="github_issue",
        status="success",
        coverage_status="complete",
        pages_fetched=1,
        output_count=1,
    )

    async def fake_collect(*args: object, **kwargs: object) -> CollectionBatch:
        del args, kwargs
        raw = {
            "id": 77,
            "source_type": "github_issue",
            "source_name": "example/project",
            "title": "Late project issue",
            "content": version["content"],
            "url": "https://example.test/issues/77",
            "published_at": old_published.isoformat(),
            "source_authority": "maintainer",
        }
        return CollectionBatch(events=[raw], failed_sources=[], source_tasks=[task])

    monkeypatch.setattr(workflow, "_collect_watchlist", fake_collect)

    first = await workflow.scan(window_mode="since_last", scan_id="late-one")
    assert first.all_change_count == 1
    first_item = workflow.ledger.list_scan_changes("late-one", limit=10).items[0]
    assert first_item["event"]["raw_payload"]["window_exception"] == "late_discovery"

    version["content"] = "revised observation"
    second = await workflow.scan(window_mode="since_last", scan_id="late-two")
    assert second.all_change_count == 1
    second_item = workflow.ledger.list_scan_changes("late-two", limit=10).items[0]
    assert second_item["event"]["raw_payload"]["window_exception"] == "late_revision"

    third = await workflow.scan(window_mode="since_last", scan_id="late-three")
    assert third.all_change_count == 0

@pytest.mark.asyncio
async def test_non_interactive_scan_never_advances_interactive_checkpoint(
    project_root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workflow = SignalHarnessWorkflow(
        cwd=project_root,
        output_dir=tmp_path / "out",
        state_dir=tmp_path / "state",
        mode=RunMode.DEMO,
        project_id="signalharness",
    )
    task = SourceTask(
        task_id="scheduled-source",
        source_name="example/project",
        source_type="github_issue",
        status="success",
        coverage_status="complete",
        pages_fetched=1,
        output_count=1,
    )

    async def fake_collect(*args: object, **kwargs: object) -> CollectionBatch:
        del args, kwargs
        return CollectionBatch(
            events=[_event("scheduled", datetime.now(timezone.utc) - timedelta(hours=1))],
            failed_sources=[],
            source_tasks=[task],
        )

    monkeypatch.setattr(workflow, "_collect_watchlist", fake_collect)
    await workflow.scan(window_mode="24h", interactive=False, scan_id="scheduled-one")
    assert workflow.ledger.get_interactive_checkpoint(project_id="signalharness") is None


def test_since_last_does_not_treat_future_fact_as_late_discovery(
    project_root: Path, tmp_path: Path
) -> None:
    workflow = SignalHarnessWorkflow(
        cwd=project_root,
        output_dir=tmp_path / "out",
        state_dir=tmp_path / "state",
        mode=RunMode.DEMO,
        project_id="signalharness",
    )
    now = datetime.now(timezone.utc)
    workflow.ledger.advance_interactive_checkpoint(
        project_id="signalharness",
        checkpoint_at=now - timedelta(days=1),
        scan_id="prior",
    )
    window = resolve_scan_window(
        ledger=workflow.ledger,
        project_id="signalharness",
        mode="since_last",
        now=now,
    )
    event = workflow._normalize_collected(_event("future", now + timedelta(seconds=1)))
    assert workflow._select_for_window(event, window) is None
