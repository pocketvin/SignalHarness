"""Static HTML dashboard writer for SignalHarness outputs."""

from __future__ import annotations

import html
import json
from collections import Counter
from pathlib import Path
from typing import Any

from signal_harness.utils.fs import atomic_write_text


def write_dashboard(output_dir: str | Path) -> Path:
    """Write outputs/dashboard.html from local JSON artifacts."""

    root = Path(output_dir).expanduser().resolve()
    signals = _read_json(root / "signals.json", [])
    assessments = _read_json(root / "impact_scores.json", [])
    trace = _read_json(root / "task_trace.json", [])
    alerts = _read_json(root / "alerts.json", [])
    learning = _read_json(root / "latest_learning_observation.json", {})
    learning_staging = _read_json(root / "latest_learning_staging.json", {})
    model_eval = _read_json(root / "model_eval_summary.json", {})
    source_tasks = [
        task
        for step in trace
        if isinstance(step, dict)
        for task in step.get("source_tasks", [])
        if isinstance(task, dict)
    ]
    html_text = _render_dashboard(
        signals=signals if isinstance(signals, list) else [],
        assessments=assessments if isinstance(assessments, list) else [],
        trace=trace if isinstance(trace, list) else [],
        alerts=alerts if isinstance(alerts, list) else [],
        learning=learning if isinstance(learning, dict) else {},
        learning_staging=learning_staging if isinstance(learning_staging, dict) else {},
        model_eval=model_eval if isinstance(model_eval, dict) else {},
        source_tasks=source_tasks,
    )
    path = root / "dashboard.html"
    atomic_write_text(path, html_text)
    return path


def _read_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return default


def _render_dashboard(
    *,
    signals: list[dict[str, Any]],
    assessments: list[dict[str, Any]],
    trace: list[dict[str, Any]],
    alerts: list[dict[str, Any]],
    learning: dict[str, Any],
    learning_staging: dict[str, Any],
    model_eval: dict[str, Any],
    source_tasks: list[dict[str, Any]],
) -> str:
    event_by_id = {str(item.get("event_id")): item for item in signals}
    counts = Counter(str(item.get("decision", "unknown")) for item in assessments)
    high_priority = [
        item
        for item in assessments
        if item.get("decision") in {"action_required", "alert"}
    ]
    top_modules = Counter(
        module
        for item in assessments
        for module in item.get("affected_modules", [])
        if isinstance(module, str)
    )
    tool_steps = [
        step
        for step in trace
        if step.get("tools_requested")
        or step.get("tools_executed")
        or step.get("blocked_tools")
        or step.get("budget_blocked_count")
    ]
    failed_sources = [
        source
        for step in trace
        for source in step.get("failed_sources", [])
        if isinstance(source, str)
    ]
    repair_steps = [
        step
        for step in trace
        if step.get("step")
        in {"repair_requested", "repair_context_evidence", "repair_impact", "repair_action", "repair_blocked"}
    ]
    llm_step = next((step for step in trace if step.get("step") == "llm_agent_call"), {})
    limit_step = next((step for step in trace if step.get("step") == "agent_loop_limits"), {})
    learning_summary = str(learning.get("learning_summary", "No learning observation."))
    cards = [
        _metric("Signals", len(signals)),
        _metric("Assessments", len(assessments)),
        _metric("Alerts", len(alerts)),
        _metric("Action required", counts["action_required"]),
    ]
    rows = "\n".join(
        _signal_row(item, event_by_id.get(str(item.get("event_id")), {}))
        for item in sorted(
            assessments,
            key=lambda value: float(value.get("impact_score", 0)),
            reverse=True,
        )
    )
    alert_items = "".join(
        f"<li><strong>{_e(alert.get('title'))}</strong> — {_e(', '.join(alert.get('reasons', [])))}</li>"
        for alert in alerts
    ) or "<li>No new alerts.</li>"
    trace_items = "".join(
        "<li>"
        f"{_e(step.get('agent_name') or step.get('agent') or step.get('step'))}: "
        f"requested={len(step.get('tools_requested', []))}, "
        f"executed={len(step.get('tools_executed', []))}, "
        f"blocked={len(step.get('blocked_tools', []))}, "
        f"budget={step.get('budget_blocked_count') or 0}, "
        f"exit={_e(step.get('exit_condition') or '')}"
        "</li>"
        for step in tool_steps
    ) or "<li>No tool calls recorded.</li>"
    repair_items = _repair_table(repair_steps)
    score_items = "".join(
        _score_breakdown_row(item)
        for item in sorted(
            assessments,
            key=lambda value: float(value.get("impact_score", 0)),
            reverse=True,
        )[:12]
    ) or "<tr><td colspan=\"6\">No score breakdowns recorded.</td></tr>"
    staging_items = "".join(
        "<li>"
        f"{_e(item.get('proposal_id'))}: "
        f"status={_e(item.get('status'))}, "
        f"risk={_e((item.get('risk') or {}).get('risk_level'))}, "
        f"replay_gate={_e((item.get('risk') or {}).get('replay_gate_passed'))}"
        "</li>"
        for item in learning_staging.get("proposals", [])
        if isinstance(item, dict)
    ) or "<li>No staged learning proposals.</li>"
    model_items = "".join(
        [
            f"<li>provider: {_e(model_eval.get('provider') or llm_step.get('mode') or 'n/a')}</li>",
            f"<li>model: {_e(model_eval.get('model') or llm_step.get('model') or 'n/a')}</li>",
            f"<li>model_profile: {_e(model_eval.get('model_profile') or 'n/a')}</li>",
            f"<li>run_state_mode: {_e(model_eval.get('run_state_mode') or 'n/a')}</li>",
            f"<li>limits: {_e(limit_step.get('detail') or 'n/a')}</li>",
        ]
    )
    source_items = "".join(
        f"<li>{_e(task.get('source_type'))}:{_e(task.get('source_name'))} — {_e(task.get('status'))}</li>"
        for task in source_tasks
    ) or "<li>No source tasks recorded.</li>"
    module_items = "".join(
        f"<li>{_e(module)}: {count}</li>"
        for module, count in top_modules.most_common(8)
    ) or "<li>No affected modules.</li>"
    failed_items = "".join(f"<li>{_e(item)}</li>" for item in failed_sources) or "<li>None.</li>"
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>SignalHarness Dashboard</title>
  <style>
    body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; margin: 2rem; background: #0f172a; color: #e2e8f0; }}
    a {{ color: #38bdf8; }}
    .grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); gap: 1rem; }}
    .card, section {{ background: #111827; border: 1px solid #334155; border-radius: 12px; padding: 1rem; margin: 1rem 0; }}
    .metric {{ font-size: 2rem; font-weight: 700; }}
    table {{ width: 100%; border-collapse: collapse; }}
    th, td {{ border-bottom: 1px solid #334155; padding: .6rem; text-align: left; vertical-align: top; }}
    th {{ color: #93c5fd; }}
    .muted {{ color: #94a3b8; }}
    .pill {{ display: inline-block; padding: .15rem .5rem; border-radius: 999px; background: #1e293b; }}
  </style>
</head>
<body>
  <h1>SignalHarness Dashboard</h1>
  <p class="muted">Static local dashboard. No server, CDN, external telemetry, or notification dispatch.</p>
  <div class="grid">{''.join(cards)}</div>
  <section>
    <h2>High priority signals</h2>
    <p>{len(high_priority)} action-required or alert signals.</p>
    <table><thead><tr><th>Signal</th><th>Decision</th><th>Score</th><th>Reason</th></tr></thead><tbody>{rows}</tbody></table>
  </section>
  <section><h2>Alerts</h2><ul>{alert_items}</ul></section>
  <section><h2>Source health</h2><ul>{source_items}</ul><h3>Failed sources</h3><ul>{failed_items}</ul></section>
  <section><h2>Top affected modules</h2><ul>{module_items}</ul></section>
  <section><h2>Model, profile, and limits</h2><ul>{model_items}</ul></section>
  <section><h2>Agent trace and tools</h2><ul>{trace_items}</ul></section>
  <section><h2>Agent repair pass</h2>{repair_items}</section>
  <section>
    <h2>Score breakdown</h2>
    <table><thead><tr><th>Signal</th><th>Final</th><th>Base</th><th>Semantic</th><th>Evidence</th><th>Weights</th></tr></thead><tbody>{score_items}</tbody></table>
  </section>
  <section><h2>Learning proposal summary</h2><p>{_e(learning_summary)}</p></section>
  <section><h2>Learning staging</h2><ul>{staging_items}</ul></section>
</body>
</html>
"""


def _metric(label: str, value: int) -> str:
    return f'<div class="card"><div class="muted">{_e(label)}</div><div class="metric">{value}</div></div>'


def _signal_row(assessment: dict[str, Any], event: dict[str, Any]) -> str:
    title = event.get("title") or assessment.get("event_id")
    url = event.get("url")
    title_html = _e(title)
    if url:
        title_html = f'<a href="{_e(url)}">{title_html}</a>'
    return (
        "<tr>"
        f"<td>{title_html}</td>"
        f"<td><span class=\"pill\">{_e(assessment.get('decision'))}</span></td>"
        f"<td>{float(assessment.get('impact_score', 0)):.1f}</td>"
        f"<td>{_e(assessment.get('reason'))}</td>"
        "</tr>"
    )


def _score_breakdown_row(assessment: dict[str, Any]) -> str:
    agent = assessment.get("agent_score_breakdown")
    if isinstance(agent, dict):
        return (
            "<tr>"
            f"<td>{_e(assessment.get('event_id'))}</td>"
            f"<td>{float(agent.get('final_score', 0)):.1f}</td>"
            f"<td>{float(agent.get('deterministic_base_score', 0)):.1f}</td>"
            f"<td>{float(agent.get('semantic_relevance', 0)):.1f}</td>"
            f"<td>{float(agent.get('evidence_confidence_score', 0)):.1f}</td>"
            f"<td>{_e(agent.get('deterministic_weight'))}/"
            f"{_e(agent.get('semantic_weight'))}/"
            f"{_e(agent.get('evidence_weight'))}</td>"
            "</tr>"
        )
    deterministic = assessment.get("score_breakdown")
    if isinstance(deterministic, dict):
        return (
            "<tr>"
            f"<td>{_e(assessment.get('event_id'))}</td>"
            f"<td>{float(deterministic.get('final_score', 0)):.1f}</td>"
            f"<td>{float(deterministic.get('project_relevance_score', 0)):.1f}</td>"
            f"<td>{float(assessment.get('relevance_score', 0)):.1f}</td>"
            f"<td>{float(assessment.get('confidence', 0)) * 100:.1f}</td>"
            f"<td>deterministic</td>"
            "</tr>"
        )
    return (
        "<tr>"
        f"<td>{_e(assessment.get('event_id'))}</td>"
        f"<td>{float(assessment.get('impact_score', 0)):.1f}</td>"
        "<td colspan=\"4\">No structured breakdown</td>"
        "</tr>"
    )


def _repair_table(repair_steps: list[dict[str, Any]]) -> str:
    if not repair_steps:
        return "<p>No repair pass was triggered.</p>"
    rows = "".join(_repair_row(step) for step in repair_steps)
    return (
        "<table>"
        "<thead><tr>"
        "<th>Step</th><th>Triggered by</th><th>Target</th><th>Events</th>"
        "<th>Status</th><th>Round</th><th>Reason / Blocked reason</th>"
        "</tr></thead>"
        f"<tbody>{rows}</tbody>"
        "</table>"
    )


def _repair_row(step: dict[str, Any]) -> str:
    repair = _repair_info(step)
    reason = repair.get("blocked_reason") or repair.get("reason") or step.get("detail")
    events = repair.get("event_ids")
    if isinstance(events, list):
        event_text = ", ".join(str(item) for item in events)
    else:
        event_text = str(events or "")
    return (
        "<tr>"
        f"<td>{_e(step.get('step'))}</td>"
        f"<td>{_e(repair.get('triggered_by'))}</td>"
        f"<td>{_e(repair.get('target_agent'))}</td>"
        f"<td>{_e(event_text)}</td>"
        f"<td>{_e(step.get('status'))}</td>"
        f"<td>{_e(repair.get('repair_round'))}</td>"
        f"<td>{_e(reason)}</td>"
        "</tr>"
    )


def _repair_info(step: dict[str, Any]) -> dict[str, Any]:
    metadata = step.get("metadata")
    if isinstance(metadata, dict):
        repair = metadata.get("repair")
        if isinstance(repair, dict) and repair:
            return dict(repair)
    detail = str(step.get("detail") or "")
    event_ids = _detail_value(detail, "event_ids")
    return {
        "triggered_by": _detail_value(detail, "triggered_by"),
        "target_agent": _detail_value(detail, "target_agent"),
        "event_ids": [
            item.strip() for item in event_ids.split(",") if item.strip()
        ],
        "repair_round": "",
        "reason": _detail_value(detail, "reason") or detail,
        "blocked_reason": "",
    }


def _detail_value(detail: str, key: str) -> str:
    marker = f"{key}="
    if marker not in detail:
        return ""
    tail = detail.split(marker, 1)[1]
    return tail.split(";", 1)[0].strip()


def _e(value: object) -> str:
    return html.escape(str(value or ""))
