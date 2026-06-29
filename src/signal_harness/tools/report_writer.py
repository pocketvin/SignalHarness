"""Structured JSON and Markdown report generation."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Iterable

from signal_harness.utils.fs import atomic_write_text
from signal_harness.signal.schemas import (
    SignalAssessment,
    SignalDecision,
    SignalEvent,
    SourceTask,
    TraceStep,
)


def _json(path: Path, payload: object) -> None:
    atomic_write_text(path, json.dumps(payload, indent=2, ensure_ascii=False) + "\n")


def write_json_outputs(
    output_dir: str | Path,
    signals: Iterable[SignalEvent],
    assessments: Iterable[SignalAssessment],
    action_items: list[dict[str, object]],
    trace: Iterable[TraceStep],
) -> dict[str, Path]:
    """Write all machine-readable scan outputs."""

    root = Path(output_dir).expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    signal_list = list(signals)
    assessment_list = list(assessments)
    trace_list = list(trace)
    paths = {
        "signals": root / "signals.json",
        "impact_scores": root / "impact_scores.json",
        "action_items": root / "action_items.json",
        "task_trace": root / "task_trace.json",
    }
    _json(paths["signals"], [item.model_dump(mode="json") for item in signal_list])
    _json(
        paths["impact_scores"],
        [item.model_dump(mode="json") for item in assessment_list],
    )
    _json(paths["action_items"], action_items)
    _json(paths["task_trace"], [item.model_dump(mode="json") for item in trace_list])
    return paths


def write_radar_digest(
    output_dir: str | Path,
    signals: Iterable[SignalEvent],
    assessments: Iterable[SignalAssessment],
) -> Path:
    """Write the human-readable radar digest grouped by decision severity."""

    root = Path(output_dir).expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    event_by_id = {event.event_id: event for event in signals}
    groups = (
        (
            "High Priority",
            lambda item: item.decision
            in {SignalDecision.ACTION_REQUIRED, SignalDecision.ALERT},
        ),
        (
            "Medium Priority",
            lambda item: item.decision is SignalDecision.SAVE and item.impact_score >= 60,
        ),
        (
            "Low Priority / Saved",
            lambda item: item.decision is SignalDecision.SAVE and item.impact_score < 60,
        ),
        ("Ignored / Noise", lambda item: item.decision is SignalDecision.IGNORE),
    )
    assessments_list = list(assessments)
    lines = ["# SignalHarness Radar Digest", ""]
    for title, selector in groups:
        lines.extend([f"## {title}", ""])
        selected = [item for item in assessments_list if selector(item)]
        if not selected:
            lines.extend(["_No signals in this section._", ""])
            continue
        for assessment in sorted(selected, key=lambda item: item.impact_score, reverse=True):
            event = event_by_id.get(assessment.event_id)
            lines.extend(
                [
                    f"### {event.title if event else assessment.event_id}",
                    "",
                    f"- Source: {event.source_name if event else 'unknown'}",
                    f"- Category: {assessment.category.value}",
                    f"- Impact score: {assessment.impact_score:.2f}",
                    f"- Confidence: {assessment.confidence:.2f}",
                    "- Affected modules: "
                    + (", ".join(assessment.affected_modules) or "None identified"),
                    "- Evidence: " + (", ".join(assessment.evidence_urls) or "No URL supplied"),
                    f"- Reason: {assessment.reason or 'No explanation supplied'}",
                    "- Action items:",
                ]
            )
            lines.extend(
                [f"  - {item}" for item in assessment.action_items]
                or ["  - No action required"]
            )
            lines.append("")
    path = root / "radar_digest.md"
    atomic_write_text(path, "\n".join(lines).rstrip() + "\n")
    return path


def write_run_summary(
    output_dir: str | Path,
    signals: Iterable[SignalEvent],
    assessments: Iterable[SignalAssessment],
    failed_sources: Iterable[str] = (),
    source_tasks: Iterable[SourceTask] = (),
) -> Path:
    """Write a compact plain-text summary for shell demos and CI artifacts."""

    root = Path(output_dir).expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    signal_list = list(signals)
    assessment_list = list(assessments)
    failure_list = list(failed_sources)
    task_list = list(source_tasks)
    counts = {
        decision: sum(item.decision is decision for item in assessment_list)
        for decision in SignalDecision
    }
    lines = [
        "SignalHarness Run Summary",
        f"Signals: {len(signal_list)}",
        f"Assessments: {len(assessment_list)}",
        f"Action required: {counts[SignalDecision.ACTION_REQUIRED]}",
        f"Alerts: {counts[SignalDecision.ALERT]}",
        f"Saved: {counts[SignalDecision.SAVE]}",
        f"Ignored: {counts[SignalDecision.IGNORE]}",
        f"Failed sources: {len(failure_list)}",
        f"Source tasks: {len(task_list)}",
    ]
    lines.extend(f"- {failure}" for failure in failure_list)
    lines.extend(
        (
            f"- task {task.source_type}:{task.source_name} "
            f"status={task.status} output={task.output_count} "
            f"duration_ms={task.duration_ms} cache_hit={str(task.cache_hit).lower()}"
            + (f" error={task.error}" if task.error else "")
        )
        for task in task_list
    )
    path = root / "run_summary.txt"
    atomic_write_text(path, "\n".join(lines) + "\n")
    return path
