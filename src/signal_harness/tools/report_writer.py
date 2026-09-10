"""Structured JSON and Markdown report generation."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Iterable

from signal_harness.presentation import sanitize_user_facing_actions
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
    """Write a human-facing Chinese radar digest; audit truth stays in JSON/Trace."""

    root = Path(output_dir).expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    event_by_id = {event.event_id: event for event in signals}
    groups = (
        (
            "重点关注",
            lambda item: item.decision
            in {SignalDecision.ACTION_REQUIRED, SignalDecision.ALERT},
        ),
        (
            "值得保留",
            lambda item: item.decision is SignalDecision.SAVE and item.impact_score >= 60,
        ),
        (
            "低优先级 / 已保存",
            lambda item: item.decision is SignalDecision.SAVE and item.impact_score < 60,
        ),
        ("已忽略 / 噪声", lambda item: item.decision is SignalDecision.IGNORE),
    )
    assessments_list = list(assessments)
    report_summary = next(
        (item.report_summary_zh.strip() for item in assessments_list if item.report_summary_zh.strip()),
        "",
    )
    lines = ["# SignalHarness 项目环境雷达", ""]
    if report_summary:
        lines.extend([report_summary, ""])
    for title, selector in groups:
        lines.extend([f"## {title}", ""])
        selected = [item for item in assessments_list if selector(item)]
        if not selected:
            lines.extend(["_这一组目前没有变化。_", ""])
            continue
        for assessment in sorted(selected, key=lambda item: item.impact_score, reverse=True):
            event = event_by_id.get(assessment.event_id)
            event_title = event.title if event else assessment.event_id
            what = assessment.what_changed_zh.strip()
            if not what:
                what = f"检测到「{event_title}」这条项目环境变化。"
            why = assessment.why_relevant_zh.strip()
            if not why:
                modules = "、".join(assessment.affected_modules[:3]) or "项目当前使用方式"
                why = f"这条变化与 {modules} 有关联，建议结合当前版本和实现方式确认实际影响。"
            actions = sanitize_user_facing_actions(assessment.action_items_zh)
            lines.extend(
                [
                    f"### {event_title}",
                    "",
                    f"- 来源：{event.source_name if event else '未知'}",
                    f"- 分类：{assessment.category.value}",
                    f"- 影响分：{assessment.impact_score:.2f}",
                    f"- 置信度：{assessment.confidence:.2f}",
                    "- 影响模块："
                    + ("、".join(assessment.affected_modules) or "暂未识别"),
                    "",
                    "**发生了什么**",
                    "",
                    what,
                    "",
                    "**为什么与你有关**",
                    "",
                    why,
                    "",
                    "**建议怎么做**",
                    "",
                ]
            )
            lines.extend([f"- {item}" for item in actions] or ["- 暂无需要立即执行的动作。"] )
            if event and event.url:
                lines.extend(["", f"原始来源：{event.url}"])
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
