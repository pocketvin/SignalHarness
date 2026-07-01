"""Static HTML dashboard writer for SignalHarness outputs."""

from __future__ import annotations

import html
import json
import re
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
    sorted_assessments = sorted(
        assessments,
        key=lambda value: _to_float(value.get("impact_score")),
        reverse=True,
    )
    high_priority_sorted = sorted(
        high_priority,
        key=lambda value: _to_float(value.get("impact_score")),
        reverse=True,
    )
    top_modules = Counter(
        module
        for item in assessments
        for module in _strings(item.get("affected_modules"))
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
        in {
            "repair_requested",
            "repair_context_evidence",
            "repair_impact",
            "repair_action",
            "repair_blocked",
        }
    ]
    llm_step = next((step for step in trace if step.get("step") == "llm_agent_call"), {})
    limit_step = next((step for step in trace if step.get("step") == "agent_loop_limits"), {})
    health = _llm_health(trace)
    cards = [
        _metric("Signals", len(signals)),
        _metric("Assessments", len(assessments)),
        _metric("Alerts", len(alerts)),
        _metric("Action required", counts["action_required"]),
    ]
    banner = _fallback_banner(health)
    executive = _executive_summary(
        signals=signals,
        assessments=assessments,
        alerts=alerts,
        source_tasks=source_tasks,
        failed_sources=failed_sources,
        top_modules=top_modules,
        health=health,
        event_by_id=event_by_id,
    )
    rows = "\n".join(
        _signal_row(item, event_by_id.get(str(item.get("event_id")), {}))
        for item in high_priority_sorted[:12]
    ) or "<tr><td colspan=\"4\">No action-required or alert signals.</td></tr>"
    alert_items = "".join(
        f"<li><strong>{_e(alert.get('title'))}</strong> — {_e(', '.join(_strings(alert.get('reasons'))))}</li>"
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
        for item in sorted_assessments[:12]
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
    learning_section = _learning_section(
        learning=learning,
        staging_items=staging_items,
        fallback_used=bool(health["fallback_count"]),
    )
    model_items = "".join(
        [
            f"<li>provider: {_e(model_eval.get('provider') or llm_step.get('mode') or 'n/a')}</li>",
            f"<li>model: {_e(model_eval.get('model') or llm_step.get('model') or 'n/a')}</li>",
            f"<li>model_profile: {_e(model_eval.get('model_profile') or 'n/a')}</li>",
            f"<li>run_state_mode: {_e(model_eval.get('run_state_mode') or 'n/a')}</li>",
            f"<li>llm_agent_call_count: {health['llm_agent_call_count']}</li>",
            f"<li>schema_failures: {health['schema_failures']}</li>",
            f"<li>fallback_count: {health['fallback_count']}</li>",
            f"<li>retry_total: {health['retry_total']}</li>",
            f"<li>timeout_count: {health['timeout_count']}</li>",
            f"<li>limits: {_e(limit_step.get('detail') or 'n/a')}</li>",
        ]
    )
    source_items = "".join(
        f"<li>{_e(task.get('source_type'))}:{_e(task.get('source_name'))} — "
        f"{_e(task.get('status'))} ({_e(task.get('output_count'))} outputs)</li>"
        for task in source_tasks
    ) or "<li>No source tasks recorded.</li>"
    module_items = "".join(
        f"<li>{_e(module)}: {count}</li>"
        for module, count in top_modules.most_common(8)
    ) or "<li>No affected modules.</li>"
    failed_items = "".join(f"<li>{_e(item)}</li>" for item in failed_sources) or "<li>None.</li>"
    dependency_groups = _dependency_groups(assessments, event_by_id)
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
    .pill {{ display: inline-block; padding: .15rem .5rem; margin: .1rem .15rem .1rem 0; border-radius: 999px; background: #1e293b; }}
    .warning {{ border-color: #f59e0b; background: #2b1f0b; }}
    .signal-title {{ font-weight: 650; }}
    .signal-subtitle {{ margin-top: .25rem; font-size: .86rem; color: #94a3b8; }}
    .tags {{ margin-top: .4rem; }}
    .columns {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(260px, 1fr)); gap: 1rem; }}
  </style>
</head>
<body>
  <h1>SignalHarness Dashboard</h1>
  <p class="muted">Static local dashboard. No server, CDN, external telemetry, or notification dispatch.</p>
  <div class="grid">{''.join(cards)}</div>
  {banner}
  {executive}
  <section>
    <h2>High priority signals</h2>
    <p>{len(high_priority)} action-required or alert signals. Showing top {min(12, len(high_priority))} by score.</p>
    <table><thead><tr><th>Signal</th><th>Tags</th><th>Score</th><th>Reason</th></tr></thead><tbody>{rows}</tbody></table>
  </section>
  <section><h2>Grouped dependency updates</h2>{dependency_groups}</section>
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
  {learning_section}
</body>
</html>
"""


def _metric(label: str, value: int) -> str:
    return f'<div class="card"><div class="muted">{_e(label)}</div><div class="metric">{value}</div></div>'


def _executive_summary(
    *,
    signals: list[dict[str, Any]],
    assessments: list[dict[str, Any]],
    alerts: list[dict[str, Any]],
    source_tasks: list[dict[str, Any]],
    failed_sources: list[str],
    top_modules: Counter[str],
    health: dict[str, int | bool],
    event_by_id: dict[str, dict[str, Any]],
) -> str:
    source_statuses = Counter(str(task.get("status") or "unknown") for task in source_tasks)
    source_types = Counter(str(item.get("source_type") or "unknown") for item in signals)
    categories = Counter(str(item.get("category") or "unknown") for item in assessments)
    action_required = sum(
        1 for item in assessments if item.get("decision") == "action_required"
    )
    recommendations = _recommended_actions(assessments, event_by_id)
    source_summary = ", ".join(
        f"{status}={count}" for status, count in sorted(source_statuses.items())
    ) or "n/a"
    source_type_items = _list_items(source_types.most_common(5))
    category_items = _list_items(categories.most_common(5))
    module_items = _list_items(top_modules.most_common(5))
    recommendation_items = "".join(
        f"<li>{_e(item)}</li>" for item in recommendations
    ) or "<li>No immediate action recommendation.</li>"
    fallback_text = (
        "yes"
        if health["fallback_count"]
        or health["retry_total"]
        or health["timeout_count"]
        or health["schema_failures"]
        else "no"
    )
    return f"""
  <section>
    <h2>信号总览 / Executive Summary</h2>
    <div class="columns">
      <div>
        <ul>
          <li>Collected signals: {len(signals)}</li>
          <li>Assessments: {len(assessments)}</li>
          <li>Alerts: {len(alerts)}</li>
          <li>Action required: {action_required}</li>
          <li>Source health: {_e(source_summary)}</li>
          <li>Failed sources: {len(failed_sources)}</li>
          <li>Fallback/retry/timeout triggered: {_e(fallback_text)}</li>
        </ul>
      </div>
      <div><h3>Signal types</h3><ul>{source_type_items}</ul></div>
      <div><h3>Important categories</h3><ul>{category_items}</ul></div>
      <div><h3>Affected modules</h3><ul>{module_items}</ul></div>
    </div>
    <h3>Recommended actions</h3>
    <ol>{recommendation_items}</ol>
  </section>
"""


def _fallback_banner(health: dict[str, int | bool]) -> str:
    if not (
        health["schema_failures"]
        or health["fallback_count"]
        or health["retry_total"]
        or health["timeout_count"]
        or health["agent_team_run_timeout"]
    ):
        return ""
    return f"""
  <section class="warning">
    <h2>LLM fallback health notice</h2>
    <p>本次运行触发 LLM fallback，当前 dashboard 包含 deterministic fallback audit output。结果可用于审计和展示系统兜底能力，但不代表完整稳定的 LLM reasoning run。</p>
    <ul>
      <li>llm_agent_call_count: {health['llm_agent_call_count']}</li>
      <li>schema_failures: {health['schema_failures']}</li>
      <li>fallback_count: {health['fallback_count']}</li>
      <li>retry_total: {health['retry_total']}</li>
      <li>timeout_count: {health['timeout_count']}</li>
    </ul>
  </section>
"""


def _llm_health(trace: list[dict[str, Any]]) -> dict[str, int | bool]:
    llm_steps = [step for step in trace if step.get("step") == "llm_agent_call"]
    timeout_count = 0
    for step in trace:
        text = " ".join(
            str(step.get(key) or "")
            for key in ("detail", "error", "schema_error", "exit_condition")
        ).lower()
        if "timeout" in text or "timed out" in text:
            timeout_count += 1
    return {
        "llm_agent_call_count": len(llm_steps),
        "schema_failures": sum(
            1
            for step in llm_steps
            if step.get("schema_valid") is False or bool(step.get("schema_error"))
        ),
        "fallback_count": sum(1 for step in trace if bool(step.get("fallback_used"))),
        "retry_total": sum(int(step.get("retry_count") or 0) for step in llm_steps),
        "timeout_count": timeout_count,
        "agent_team_run_timeout": any(
            step.get("step") == "agent_team_run_timeout" for step in trace
        ),
    }


def _recommended_actions(
    assessments: list[dict[str, Any]],
    event_by_id: dict[str, dict[str, Any]],
) -> list[str]:
    selected = sorted(
        assessments,
        key=lambda item: _to_float(item.get("impact_score")),
        reverse=True,
    )
    recommendations: list[str] = []
    for assessment in selected:
        if assessment.get("decision") not in {"action_required", "alert"}:
            continue
        event = event_by_id.get(str(assessment.get("event_id")), {})
        title = _display_title(event, assessment)
        actions = _strings(assessment.get("action_items"))
        action = actions[0] if actions else f"Review {title}"
        recommendations.append(f"{title}: {action}")
        if len(recommendations) >= 5:
            break
    return recommendations


def _signal_row(assessment: dict[str, Any], event: dict[str, Any]) -> str:
    title = _display_title(event, assessment)
    url = str(event.get("url") or "")
    title_html = _linked_title(title, url)
    subtitle = _source_subtitle(event)
    tags = _signal_tags(assessment)
    return (
        "<tr>"
        f"<td><div class=\"signal-title\">{title_html}</div>"
        f"<div class=\"signal-subtitle\">{subtitle}</div></td>"
        f"<td><div class=\"tags\">{tags}</div></td>"
        f"<td>{_to_float(assessment.get('impact_score')):.1f}</td>"
        f"<td>{_e(assessment.get('reason'))}</td>"
        "</tr>"
    )


def _display_title(event: dict[str, Any], assessment: dict[str, Any]) -> str:
    raw_title = str(event.get("title") or assessment.get("event_id") or "Untitled")
    title = raw_title.strip()
    content = str(event.get("content") or "")
    if title.isdigit() or len(title) <= 3:
        title = _first_sentence(content) or title
    source_type = str(event.get("source_type") or "")
    source_name = str(event.get("source_name") or "unknown")
    if source_type == "github_release":
        return f"[Release] {source_name}: {title}"
    if source_type == "github_issue":
        return f"[Issue] {source_name}: {title}"
    if source_type == "rss":
        return f"[RSS] {source_name}: {title}"
    if source_type == "web_change":
        return f"[Web] {source_name}: {title}"
    return f"[{source_type or 'Signal'}] {source_name}: {title}"


def _source_subtitle(event: dict[str, Any]) -> str:
    parts = [
        str(event.get("source_type") or "unknown"),
        str(event.get("source_name") or "unknown"),
    ]
    published = event.get("published_at")
    if published:
        parts.append(str(published))
    return _e(" / ".join(parts))


def _signal_tags(assessment: dict[str, Any]) -> str:
    tags = [
        str(assessment.get("decision") or "unknown"),
        str(assessment.get("category") or "unknown"),
    ]
    tags.extend(_strings(assessment.get("affected_modules"))[:4])
    return "".join(f"<span class=\"pill\">{_e(tag)}</span>" for tag in tags if tag)


def _linked_title(title: str, url: str) -> str:
    title_html = _e(title)
    if not url:
        return title_html
    if _is_demo_url(url):
        return f"{title_html}<div class=\"muted\">demo/example link suppressed</div>"
    return f'<a href="{_e(url)}">{title_html}</a>'


def _is_demo_url(url: str) -> bool:
    lowered = url.lower()
    return (
        "example.com" in lowered
        or "demo-" in lowered
        or "sample-" in lowered
        or "sample_events" in lowered
    )


def _dependency_groups(
    assessments: list[dict[str, Any]],
    event_by_id: dict[str, dict[str, Any]],
) -> str:
    groups: dict[
        tuple[str, str, tuple[str, ...], str],
        list[tuple[dict[str, Any], dict[str, Any]]],
    ] = {}
    for assessment in assessments:
        event = event_by_id.get(str(assessment.get("event_id")), {})
        category = str(assessment.get("category") or "unknown")
        source_type = str(event.get("source_type") or "")
        if category != "dependency_update" and source_type != "github_release":
            continue
        modules = tuple(sorted(_strings(assessment.get("affected_modules"))))
        package = _package_name(str(event.get("title") or "dependency"))
        key = (str(event.get("source_name") or "unknown"), category, modules, package)
        groups.setdefault(key, []).append((assessment, event))
    rows = []
    for (source_name, category, modules, package), items in sorted(groups.items()):
        if len(items) < 2:
            continue
        titles = [str(event.get("title") or "") for _, event in items]
        versions = [_version_text(title) for title in titles]
        version_range = _version_range([version for version in versions if version])
        score = max(_to_float(assessment.get("impact_score")) for assessment, _ in items)
        rows.append(
            "<tr>"
            f"<td>{_e(source_name)}</td>"
            f"<td>{_e(package)} release series"
            f"{': ' + _e(version_range) if version_range else ''}</td>"
            f"<td>{len(items)}</td>"
            f"<td>{_e(category)}</td>"
            f"<td>{_e(', '.join(modules) or 'n/a')}</td>"
            f"<td>{score:.1f}</td>"
            "</tr>"
        )
    if not rows:
        return "<p>No grouped dependency release series in this run.</p>"
    return (
        "<table><thead><tr><th>Source</th><th>Group</th><th>Items</th>"
        "<th>Category</th><th>Affected modules</th><th>Top score</th></tr></thead>"
        f"<tbody>{''.join(rows)}</tbody></table>"
    )


def _score_breakdown_row(assessment: dict[str, Any]) -> str:
    agent = assessment.get("agent_score_breakdown")
    if isinstance(agent, dict):
        return (
            "<tr>"
            f"<td>{_e(assessment.get('event_id'))}</td>"
            f"<td>{_to_float(agent.get('final_score')):.1f}</td>"
            f"<td>{_to_float(agent.get('deterministic_base_score')):.1f}</td>"
            f"<td>{_to_float(agent.get('semantic_relevance')):.1f}</td>"
            f"<td>{_to_float(agent.get('evidence_confidence_score')):.1f}</td>"
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
            f"<td>{_to_float(deterministic.get('final_score')):.1f}</td>"
            f"<td>{_to_float(deterministic.get('project_relevance_score')):.1f}</td>"
            f"<td>{_to_float(assessment.get('relevance_score')):.1f}</td>"
            f"<td>{_to_float(assessment.get('confidence')) * 100:.1f}</td>"
            f"<td>deterministic</td>"
            "</tr>"
        )
    return (
        "<tr>"
        f"<td>{_e(assessment.get('event_id'))}</td>"
        f"<td>{_to_float(assessment.get('impact_score')):.1f}</td>"
        "<td colspan=\"4\">No structured breakdown</td>"
        "</tr>"
    )


def _learning_section(
    *,
    learning: dict[str, Any],
    staging_items: str,
    fallback_used: bool,
) -> str:
    learning_summary = str(learning.get("learning_summary") or "No learning observation.")
    no_apply_message = (
        "No learning was applied in this run. Learning proposals require explicit "
        "review/apply. 本次未自动应用学习结果。学习建议是 review-only，需要人工审核后才能应用。"
    )
    fallback_note = (
        "<p class=\"muted\">Fallback was used, so learning remains review-only and was not auto-applied.</p>"
        if fallback_used
        else ""
    )
    return f"""
  <section>
    <h2>Learning proposal summary</h2>
    <p>{_e(learning_summary)}</p>
    <p><strong>Learning proposals are review-only.</strong> No policy/watchlist change is auto-applied.</p>
    <p>{_e(no_apply_message)}</p>
    {fallback_note}
  </section>
  <section><h2>Learning staging</h2><ul>{staging_items}</ul></section>
"""


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


def _list_items(items: list[tuple[str, int]]) -> str:
    return "".join(
        f"<li>{_e(label)}: {count}</li>" for label, count in items
    ) or "<li>n/a</li>"


def _strings(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value if isinstance(item, str) and item]


def _to_float(value: object) -> float:
    if not isinstance(value, (int, float, str)):
        return 0.0
    try:
        return float(value)
    except ValueError:
        return 0.0


def _first_sentence(value: str) -> str:
    normalized = " ".join(value.split())
    if not normalized:
        return ""
    return normalized.split(".", 1)[0][:120]


def _package_name(title: str) -> str:
    match = re.match(r"^([A-Za-z0-9_.-]+)==", title)
    if match:
        return match.group(1)
    return title.split(" ", 1)[0] or "dependency"


def _version_text(title: str) -> str:
    match = re.match(r"^[A-Za-z0-9_.-]+==([0-9][A-Za-z0-9_.-]*)$", title.strip())
    return match.group(1) if match else ""


def _version_range(versions: list[str]) -> str:
    if not versions:
        return ""
    sorted_versions = sorted(set(versions), key=_version_key)
    if len(sorted_versions) == 1:
        return sorted_versions[0]
    return f"{sorted_versions[0]}–{sorted_versions[-1]}"


def _version_key(value: str) -> tuple[int | str, ...]:
    parts: list[int | str] = []
    for part in re.split(r"[.-]", value):
        parts.append(int(part) if part.isdigit() else part)
    return tuple(parts)


def _e(value: object) -> str:
    return html.escape(str(value or ""))
