"""Environment completion after the shared source pipeline. No legacy Agent team."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from signal_harness.intelligence.contracts import EnvironmentReport, Progress
from signal_harness.intelligence.corpus import assemble_changes
from signal_harness.intelligence.engine import EnvironmentEngine, ProgressListener
from signal_harness.intelligence.model_calls import BoundedModelCaller
from signal_harness.persistence import ChangeLedger
from signal_harness.persistence.intelligence import IntelligenceRepository
from signal_harness.providers.task_policy import TaskPolicy
from signal_harness.runtime.tracing import TraceRecorder
from signal_harness.runtime.windows import ResolvedScanWindow
from signal_harness.signal.schemas import SignalEvent, SourceTask
from signal_harness.tools.web_snapshot import (
    commit_pending_web_snapshots,
    discard_pending_web_snapshots,
)
from signal_harness.utils.fs import atomic_write_text


async def complete_environment_scan(
    *,
    ledger: ChangeLedger,
    config_dir: Path,
    state_dir: Path,
    output_dir: Path,
    project_id: str,
    scan_id: str,
    window: ResolvedScanWindow,
    profile_revision: dict[str, Any],
    policy: dict[str, Any],
    events: list[SignalEvent],
    event_change_ids: dict[str, tuple[str, int]],
    observed_count: int,
    source_tasks: list[SourceTask],
    coverage_status: str,
    checkpoint_safe: bool,
    interactive: bool,
    fixture: bool,
    consumer_id: str,
    trace: TraceRecorder,
    listener: ProgressListener | None,
    caller: BoundedModelCaller | None = None,
) -> EnvironmentReport:
    repository = IntelligenceRepository(ledger.path)
    try:
        # Every source is frozen before model interpretation; shallow is NOT a deep assessment.
        if repository.report(scan_id) is None:
            ledger.freeze_scan_changes(
                scan_id=scan_id,
                project_id=project_id,
                events=events,
                event_change_ids=event_change_ids,
                project_profile=profile_revision["effective_profile"],
                policy=policy,
                analyzed_event_ids=set(),
                assessments=[],
            )
        with trace.step("assemble_change_revisions", input_count=len(events)) as step:
            digests = assemble_changes(events, event_change_ids)
            step["output_count"] = len(digests)
        if listener:
            listener(
                Progress(
                    stage="organizing_changes",
                    message=f"已整理为 {len(digests)} 个独立变化",
                    completed=len(digests),
                    total=len(digests),
                    status="complete",
                )
            )
        engine = EnvironmentEngine(
            repository, caller or BoundedModelCaller(TaskPolicy.load(config_dir), trace), listener
        )
        report = await engine.run(
            scan_id=scan_id,
            project_id=project_id,
            profile_revision_id=profile_revision["profile_revision_id"],
            profile=profile_revision["effective_profile"],
            digests=digests,
            window=window.public_payload(),
            observed_count=observed_count,
            sources=[s.model_dump(mode="json") for s in source_tasks],
            coverage_status=coverage_status,
            data_origin="replay" if fixture else "live",
        )
        output_dir.mkdir(parents=True, exist_ok=True)
        atomic_write_text(
            output_dir / "environment_report.json", report.model_dump_json(indent=2) + "\n"
        )
        import json

        atomic_write_text(
            output_dir / "agent_trace.json",
            json.dumps(
                [s.model_dump(mode="json") for s in trace.steps], ensure_ascii=False, indent=2
            ),
        )
        commit_pending_web_snapshots(state_dir, scan_id)
        ledger.complete_scan(
            scan_id=scan_id,
            analyzed_count=0,
            relevant_count=report.counts["relevant"],
            coverage_status=coverage_status,
        )
        # Incomplete interpretation must not consume the user's since-last position.
        if (
            interactive
            and not fixture
            and window.checkpoint_eligible
            and checkpoint_safe
            and report.status == "complete"
        ):
            ledger.advance_interactive_checkpoint(
                project_id=project_id,
                checkpoint_at=window.upper,
                scan_id=scan_id,
                consumer_id=consumer_id,
            )
        return report
    except BaseException as exc:
        discard_pending_web_snapshots(state_dir, scan_id)
        ledger.fail_scan(scan_id=scan_id, error=type(exc).__name__)
        raise
