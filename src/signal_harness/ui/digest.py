"""Daily/weekly Markdown digest generation from local outputs."""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any, Literal

from signal_harness.utils.fs import atomic_write_text

DigestPeriod = Literal["daily", "weekly"]


def write_digest(output_dir: str | Path, *, period: DigestPeriod) -> Path:
    """Write outputs/daily_digest.md or outputs/weekly_digest.md."""

    root = Path(output_dir).expanduser().resolve()
    assessments = _read_json(root / "impact_scores.json", [])
    signals = _read_json(root / "signals.json", [])
    trace = _read_json(root / "task_trace.json", [])
    alerts = _read_json(root / "alerts.json", [])
    learning = _read_json(root / "latest_learning_observation.json", {})
    signal_by_id = {
        str(item.get("event_id")): item
        for item in signals
        if isinstance(item, dict)
    }
    assessment_list = [item for item in assessments if isinstance(item, dict)]
    counts = Counter(str(item.get("decision", "unknown")) for item in assessment_list)
    failed_sources = [
        source
        for step in trace
        if isinstance(step, dict)
        for source in step.get("failed_sources", [])
        if isinstance(source, str)
    ]
    repeated = [
        item
        for item in assessment_list
        if item.get("noise_reason") and "duplicate" in str(item.get("noise_reason"))
    ]
    lines = [
        f"# SignalHarness {period.title()} Digest",
        "",
        "Generated from local SignalHarness outputs. No external notification was sent.",
        "",
        "## Summary",
        "",
        f"- action_required: {counts['action_required']}",
        f"- alert: {counts['alert']}",
        f"- save: {counts['save']}",
        f"- ignore/noise: {counts['ignore']}",
        f"- new local alerts: {len(alerts) if isinstance(alerts, list) else 0}",
        "",
        "## Major events",
        "",
    ]
    major = [
        item
        for item in assessment_list
        if item.get("decision") in {"action_required", "alert"}
    ]
    if not major:
        lines.append("_No action-required or alert signals._")
    for assessment in major:
        event = signal_by_id.get(str(assessment.get("event_id")), {})
        lines.extend(
            [
                f"- **{event.get('title', assessment.get('event_id'))}** "
                f"({assessment.get('decision')}, score={float(assessment.get('impact_score', 0)):.1f})",
                f"  - reason: {assessment.get('reason', '')}",
            ]
        )
    lines.extend(["", "## Source health", ""])
    lines.append(f"- failed sources: {len(failed_sources)}")
    lines.extend(f"  - {source}" for source in failed_sources)
    lines.extend(["", "## Repeated signals", ""])
    if not repeated:
        lines.append("_No repeated/duplicate signals flagged in the latest outputs._")
    lines.extend(f"- {item.get('event_id')}: {item.get('noise_reason')}" for item in repeated)
    lines.extend(["", "## Learning proposal summary", ""])
    if isinstance(learning, dict) and learning:
        lines.append(f"- {learning.get('learning_summary', 'No summary')}")
        lines.append(
            "- memory sections: "
            + ", ".join(str(item) for item in learning.get("memory_sections_read", []))
        )
        lines.append("- requires approval: true")
    else:
        lines.append("_No learning observation available._")
    lines.extend(
        [
            "",
            "## Suggested next review actions",
            "",
            "- Review `outputs/alerts.md` for major events.",
            "- Review `outputs/radar_digest.md` for full context.",
            "- Review `.signal-harness/latest_learning_observation.json` before applying any proposal.",
            "",
        ]
    )
    path = root / f"{period}_digest.md"
    atomic_write_text(path, "\n".join(lines).rstrip() + "\n")
    return path


def _read_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return default

