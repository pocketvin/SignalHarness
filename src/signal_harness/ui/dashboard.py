"""Static HTML dashboard writer for SignalHarness outputs."""

from __future__ import annotations

import html
import json
import re
from collections import Counter
from pathlib import Path
from typing import Any

from signal_harness.presentation import sanitize_user_facing_actions
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
    high_priority_sorted = _balanced_signal_list(
        high_priority,
        event_by_id,
        limit=12,
    )
    observed_sorted = _balanced_signal_list(
        sorted_assessments,
        event_by_id,
        limit=12,
    )
    priority_display = high_priority_sorted if high_priority else observed_sorted
    priority_section_title = (
        "High priority signals" if high_priority else "No high-priority signals"
    )
    priority_section_description = (
        f"{len(high_priority)} action-required or alert signals. Showing a balanced "
        f"top {min(12, len(high_priority))} by source and category."
        if high_priority
        else (
            "No action-required or alert signals were produced in this run. "
            "Showing top observed signals for review."
        )
    )
    priority_table_heading = "" if high_priority else "<h3>Top observed signals</h3>"
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
    tool_health = _tool_health(trace)
    cards = [
        _metric("Signals", len(signals)),
        _metric("Assessments", len(assessments)),
        _metric("Alerts", len(alerts)),
        _metric("Action required", counts["action_required"]),
    ]
    banner = _health_notices(
        health,
        failed_sources=failed_sources,
        source_tasks=source_tasks,
    )
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
    signal_summary = _signal_summary(
        signals=signals,
        assessments=assessments,
        source_tasks=source_tasks,
        failed_sources=failed_sources,
        health=health,
        tool_health=tool_health,
    )
    rows = "\n".join(
        _signal_row(item, event_by_id.get(str(item.get("event_id")), {}))
        for item in priority_display[:12]
    ) or "<tr><td colspan=\"4\">No observed signals in this run.</td></tr>"
    actionable_section = ""
    if high_priority:
        actionable_rows = "\n".join(
            _signal_row(item, event_by_id.get(str(item.get("event_id")), {}))
            for item in high_priority_sorted[:8]
        ) or "<tr><td colspan=\"4\">No actionable signals in this run.</td></tr>"
        actionable_section = f"""
  <section><h2>Top actionable signals</h2><table><thead><tr><th>Signal</th><th>Tags</th><th>Score</th><th>Reason</th></tr></thead><tbody>{actionable_rows}</tbody></table></section>
"""
    runtime_rows = _section_rows(
        assessments,
        event_by_id,
        categories={
            "ecosystem_issue",
            "agent_runtime_signal",
            "checkpoint_persistence_signal",
            "structured_output_signal",
            "tool_calling_signal",
            "provider_compatibility_signal",
            "source_collection_signal",
            "security_supply_chain",
            "evaluation_benchmark_signal",
            "docs_change_signal",
        },
        limit=8,
        empty="No ecosystem/runtime signals in this run.",
    )
    insight_rows = _section_rows(
        assessments,
        event_by_id,
        categories={"expert_opinion", "market_signal"},
        source_types={"rss"},
        limit=8,
        empty="No external insight signals in this run.",
    )
    dependency_rows = _section_rows(
        assessments,
        event_by_id,
        categories={"dependency_update"},
        source_types={"github_release"},
        limit=6,
        empty="No direct dependency or release signals in this run.",
    )
    alert_items = "".join(
        f"<li><strong>{_e(alert.get('title'))}</strong> — {_e(', '.join(_strings(alert.get('reasons'))))}</li>"
        for alert in alerts
    ) or "<li>No alerts. This run produced only save/ignore decisions.</li>"
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
            f"<li>mode: {_e(model_eval.get('mode') or llm_step.get('mode') or 'n/a')}</li>",
            f"<li>provider: {_e(model_eval.get('provider') or llm_step.get('provider') or 'n/a')}</li>",
            f"<li>model: {_e(model_eval.get('model') or llm_step.get('model') or 'n/a')}</li>",
            f"<li>model_profile: {_e(model_eval.get('model_profile') or llm_step.get('model_profile') or 'n/a')}</li>",
            f"<li>run_state_mode: {_e(model_eval.get('run_state_mode') or 'n/a')}</li>",
            f"<li>llm_agent_call_count: {health['llm_agent_call_count']}</li>",
            f"<li>schema_failures: {health['schema_failures']}</li>",
            f"<li>fallback_count: {health['fallback_count']}</li>",
            f"<li>audit_completion_count: {health['audit_completion_count']}</li>",
            f"<li>retry_total: {health['retry_total']}</li>",
            f"<li>timeout_count: {health['timeout_count']}</li>",
            f"<li>tool_error_count: {tool_health['total_tool_error_count']}</li>",
            f"<li>prompt_tokens: {health['prompt_tokens']}</li>",
            f"<li>completion_tokens: {health['completion_tokens']}</li>",
            f"<li>total_tokens: {health['total_tokens']}</li>",
            f"<li>estimated_cost_usd: {health['estimated_cost_usd']:.8f}</li>",
            f"<li>usage_reported_call_count: {health['usage_reported_call_count']}</li>",
            f"<li>average_llm_latency_ms: {health['average_llm_latency_ms']:.2f}</li>",
            f"<li>limits: {_e(limit_step.get('detail') or 'n/a')}</li>",
        ]
    )
    tool_health_section = _tool_health_section(tool_health)
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
  {signal_summary}
  <section>
    <h2>{priority_section_title}</h2>
    <p>{priority_section_description}</p>
    {priority_table_heading}
    <table><thead><tr><th>Signal</th><th>Tags</th><th>Score</th><th>Reason</th></tr></thead><tbody>{rows}</tbody></table>
  </section>
  {actionable_section}
  <section><h2>Ecosystem and runtime signals</h2><table><thead><tr><th>Signal</th><th>Tags</th><th>Score</th><th>Reason</th></tr></thead><tbody>{runtime_rows}</tbody></table></section>
  <section><h2>External insights</h2><table><thead><tr><th>Signal</th><th>Tags</th><th>Score</th><th>Reason</th></tr></thead><tbody>{insight_rows}</tbody></table></section>
  <section><h2>Observed dependency updates</h2><table><thead><tr><th>Signal</th><th>Tags</th><th>Score</th><th>Reason</th></tr></thead><tbody>{dependency_rows}</tbody></table></section>
  <section><h2>Grouped dependency updates</h2>{dependency_groups}</section>
  <section><h2>Alerts</h2><ul>{alert_items}</ul></section>
  <section><h2>Source health</h2><ul>{source_items}</ul><h3>Failed sources</h3><ul>{failed_items}</ul></section>
  {tool_health_section}
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
    health: dict[str, Any],
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
    llm_health_warning = (
        "yes"
        if health["fallback_count"]
        or health["retry_total"]
        or health["timeout_count"]
        or health["schema_failures"]
        else "no"
    )
    return f"""
  <section>
    <h2>Executive Summary</h2>
    <div class="columns">
      <div>
        <ul>
          <li>Collected signals: {len(signals)}</li>
          <li>Assessments: {len(assessments)}</li>
          <li>Alerts: {len(alerts)}</li>
          <li>Action required: {action_required}</li>
          <li>Source health: {_e(source_summary)}</li>
          <li>Failed sources: {len(failed_sources)}</li>
          <li>LLM health warning: {_e(llm_health_warning)}</li>
          <li>Audit completions: {health['audit_completion_count']}</li>
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


def _signal_summary(
    *,
    signals: list[dict[str, Any]],
    assessments: list[dict[str, Any]],
    source_tasks: list[dict[str, Any]],
    failed_sources: list[str],
    health: dict[str, Any],
    tool_health: dict[str, Any],
) -> str:
    del source_tasks
    categories = Counter(str(item.get("category") or "unknown") for item in assessments)
    source_types = Counter(str(item.get("source_type") or "unknown") for item in signals)
    top_categories = ", ".join(
        f"{category} ({count})" for category, count in categories.most_common(4)
    ) or "no categorized signals"
    top_sources = ", ".join(
        f"{source_type} ({count})" for source_type, count in source_types.most_common(4)
    ) or "no collected source types"
    if categories.get("checkpoint_persistence_signal") or categories.get("tool_calling_signal") or categories.get("structured_output_signal"):
        what_changed = (
            "Runtime, tool-calling, checkpoint, or structured-output signals were present "
            "alongside broader external updates."
        )
    elif categories.get("provider_compatibility_signal"):
        what_changed = "Provider or model API compatibility signals were present."
    elif categories.get("expert_opinion"):
        what_changed = "The run mostly collected broader AI engineering insight signals."
    else:
        what_changed = "The run collected a mixed set of external project signals."
    risk_notes: list[str] = []
    if health["fallback_count"] or health["schema_failures"]:
        risk_notes.append(
            "LLM fallback or schema failures occurred; treat affected conclusions as audit-backed."
        )
    elif health["retry_total"] or health["timeout_count"]:
        risk_notes.append(
            "Provider retry warnings occurred, but structured Agent outputs recovered."
        )
    if tool_health["total_tool_error_count"]:
        risk_notes.append(
            "Tool errors reduced evidence confidence for at least one source lookup."
        )
    if failed_sources:
        risk_notes.append("Some configured sources failed and should be inspected.")
    why_matters = (
        "These signals matter because SignalHarness depends on controlled tool execution, "
        "schema validation, provider compatibility, traceable fallback, and reliable source collection."
    )
    if risk_notes:
        why_matters += " " + " ".join(risk_notes)
    next_actions = [
        "Review checkpoint, tool-calling, structured-output, provider, security, and source-collection signals first.",
        "Save broad engineering articles as context unless they directly affect provider integration, tool safety, schema reliability, or evaluation.",
        "Treat ordinary release series as observed signals unless breaking, security, or API compatibility impact is confirmed.",
    ]
    if failed_sources or tool_health["total_tool_error_count"]:
        next_actions.append("Investigate source or tool errors before relying on affected confidence scores.")
    return f"""
  <section>
    <h2>Signal Summary</h2>
    <h3>What changed?</h3>
    <p>{_e(what_changed)} Sources: {_e(top_sources)}. Categories: {_e(top_categories)}.</p>
    <h3>Why it matters?</h3>
    <p>{_e(why_matters)}</p>
    <h3>What should be done next?</h3>
    <ul>{''.join(f'<li>{_e(item)}</li>' for item in next_actions)}</ul>
  </section>
"""


def _health_notices(
    health: dict[str, Any],
    *,
    failed_sources: list[str],
    source_tasks: list[dict[str, Any]],
) -> str:
    sections: list[str] = []
    if (
        health["schema_failures"]
        or health["fallback_count"]
        or health["agent_team_run_timeout"]
    ):
        sections.append(
            f"""
  <section class="warning">
    <h2>LLM fallback health notice</h2>
    <p>This run used deterministic LLM fallback for at least one Agent call or hit the Agent-team timeout guardrail. This is useful for auditability, but affected reasoning should not be described as a fully stable LLM-only run.</p>
    <ul>
      <li>llm_agent_call_count: {health['llm_agent_call_count']}</li>
      <li>schema_failures: {health['schema_failures']}</li>
      <li>fallback_count: {health['fallback_count']}</li>
      <li>agent_team_run_timeout: {_e(health['agent_team_run_timeout'])}</li>
      <li>retry_total: {health['retry_total']}</li>
      <li>timeout_count: {health['timeout_count']}</li>
      <li>tool_error_count: {health['tool_error_count']}</li>
    </ul>
    {_provider_retry_breakdown_html(health)}
  </section>
"""
        )
    elif health["retry_total"] or health["timeout_count"]:
        sections.append(
            f"""
  <section class="warning">
    <h2>LLM retry health notice</h2>
    <p>A provider retry or timeout warning occurred, but the Agent output recovered without deterministic LLM fallback.</p>
    <ul>
      <li>llm_agent_call_count: {health['llm_agent_call_count']}</li>
      <li>retry_total: {health['retry_total']}</li>
      <li>timeout_count: {health['timeout_count']}</li>
      <li>fallback_count: {health['fallback_count']}</li>
    </ul>
    {_provider_retry_breakdown_html(health)}
  </section>
"""
        )
    if health["audit_completion_count"]:
        sections.append(
            f"""
  <section>
    <h2>Audit completion notice</h2>
    <p>Supervisor routing skipped one or more downstream LLM stages, so deterministic audit completion filled complete local assessment records. This is audit completion, not a failed downstream Agent execution.</p>
    <ul>
      <li>audit_completion_count: {health['audit_completion_count']}</li>
    </ul>
  </section>
"""
        )
    if failed_sources:
        sample_items = "".join(f"<li>{_e(item[:240])}</li>" for item in failed_sources[:3])
        breakdown_html = _source_failure_breakdown_html(source_tasks)
        sections.append(
            f"""
  <section class="warning">
    <h2>Source health warning</h2>
    <p>One or more configured live sources failed during collection. The run can still be useful, but source coverage is incomplete.</p>
    <ul>
      <li>failed_source_count: {len(failed_sources)}</li>
      {sample_items}
    </ul>
    {breakdown_html}
  </section>
"""
        )
    return "".join(sections)


def _llm_health(trace: list[dict[str, Any]]) -> dict[str, Any]:
    llm_steps = [step for step in trace if step.get("step") == "llm_agent_call"]
    timeout_count = 0
    for step in trace:
        text = " ".join(
            str(step.get(key) or "")
            for key in ("detail", "error", "schema_error", "exit_condition")
        ).lower()
        if "timeout" in text or "timed out" in text:
            timeout_count += 1
    prompt_tokens = sum(int(step.get("prompt_tokens") or 0) for step in llm_steps)
    completion_tokens = sum(int(step.get("completion_tokens") or 0) for step in llm_steps)
    total_tokens = sum(int(step.get("total_tokens") or 0) for step in llm_steps)
    estimated_cost_usd = round(
        sum(float(step.get("estimated_cost_usd") or 0.0) for step in llm_steps),
        8,
    )
    usage_reported_call_count = sum(
        1
        for step in llm_steps
        if step.get("usage_source") not in {None, "unavailable", "mock_unavailable"}
    )
    average_llm_latency_ms = (
        sum(float(step.get("duration_ms") or 0) for step in llm_steps) / len(llm_steps)
        if llm_steps
        else 0.0
    )
    return {
        "llm_agent_call_count": len(llm_steps),
        "schema_failures": sum(
            1
            for step in llm_steps
            if step.get("schema_valid") is False
        ),
        "fallback_count": sum(1 for step in llm_steps if bool(step.get("fallback_used")))
        + sum(1 for step in trace if step.get("step") == "agent_team_run_timeout"),
        "audit_completion_count": sum(
            1 for step in trace if _is_audit_completion_step(str(step.get("step") or ""))
        ),
        "retry_total": sum(int(step.get("retry_count") or 0) for step in llm_steps),
        "timeout_count": timeout_count,
        "tool_error_count": sum(len(step.get("tool_errors") or []) for step in trace),
        "provider_error_breakdown": Counter(
            _provider_error_type(step)
            for step in llm_steps
            if step.get("retry_count") or step.get("error") or step.get("schema_error")
        ),
        "agent_team_run_timeout": any(
            step.get("step") == "agent_team_run_timeout" for step in trace
        ),
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": total_tokens,
        "estimated_cost_usd": estimated_cost_usd,
        "usage_reported_call_count": usage_reported_call_count,
        "average_llm_latency_ms": average_llm_latency_ms,
    }


def _is_audit_completion_step(step_name: str) -> bool:
    return step_name in {
        "skipped_stage_audit_completion",
        "skipped_event_audit_fallback",
    }


def _provider_retry_breakdown_html(health: dict[str, Any]) -> str:
    breakdown = health.get("provider_error_breakdown")
    if not isinstance(breakdown, Counter) or not breakdown:
        return ""
    return (
        "<h3>Provider retry/error breakdown</h3><ul>"
        + _list_items(breakdown.most_common(6))
        + "</ul>"
    )


def _provider_error_type(step: dict[str, Any]) -> str:
    text = " ".join(
        str(step.get(key) or "")
        for key in ("error", "schema_error", "detail", "exit_condition")
    ).lower()
    if "connecterror" in text or "connection" in text:
        return "connect_error"
    if "readtimeout" in text or "read timeout" in text:
        return "read_timeout"
    if "timeout" in text:
        return "timeout"
    if "rate" in text and "limit" in text:
        return "rate_limit"
    if step.get("schema_valid") is False:
        return "schema_validation"
    return "provider_retry"


def _source_failure_breakdown_html(source_tasks: list[dict[str, Any]]) -> str:
    failed_tasks = [
        task
        for task in source_tasks
        if str(task.get("status") or "").lower() not in {"success", "ok"}
    ]
    if not failed_tasks:
        return ""
    by_type = Counter(str(task.get("source_type") or "unknown") for task in failed_tasks)
    by_status = Counter(str(task.get("status") or "unknown") for task in failed_tasks)
    return (
        "<div class=\"columns\">"
        "<div><h3>Failed source types</h3><ul>"
        + _list_items(by_type.most_common(8))
        + "</ul></div>"
        "<div><h3>Failure status</h3><ul>"
        + _list_items(by_status.most_common(8))
        + "</ul></div>"
        "</div>"
    )


def _tool_health(trace: list[dict[str, Any]]) -> dict[str, Any]:
    errors: list[str] = []
    tools: list[str] = []
    for step in trace:
        tools.extend(str(item) for item in step.get("tools_executed") or [])
        tools.extend(str(item) for item in step.get("blocked_tools") or [])
        errors.extend(str(item) for item in step.get("tool_errors") or [])
    unique_errors = list(dict.fromkeys(errors))
    breakdown = Counter(_tool_error_type(error) for error in unique_errors)
    affected_tools = Counter(error.split(":", 1)[0] for error in unique_errors if ":" in error)
    return {
        "total_tool_error_count": len(unique_errors),
        "error_breakdown": breakdown,
        "affected_tools": affected_tools,
        "sample_errors": unique_errors[:3],
        "tools_touched": Counter(tools),
    }


def _tool_health_section(tool_health: dict[str, Any]) -> str:
    breakdown = tool_health["error_breakdown"]
    affected = tool_health["affected_tools"]
    samples = tool_health["sample_errors"]
    breakdown_items = _list_items(breakdown.most_common(6))
    affected_items = _list_items(affected.most_common(6))
    sample_items = "".join(f"<li>{_e(item[:240])}</li>" for item in samples) or "<li>None.</li>"
    return f"""
  <section>
    <h2>Tool health</h2>
    <ul>
      <li>total_tool_error_count: {tool_health['total_tool_error_count']}</li>
    </ul>
    <div class="columns">
      <div><h3>Error type breakdown</h3><ul>{breakdown_items}</ul></div>
      <div><h3>Affected tools</h3><ul>{affected_items}</ul></div>
      <div><h3>Sample error</h3><ul>{sample_items}</ul></div>
    </div>
  </section>
"""


def _tool_error_type(error: str) -> str:
    lowered = error.lower()
    if "rss parse failed" in lowered:
        return "rss_parse_failed"
    if "request failed" in lowered:
        return "request_failed"
    if "budget" in lowered:
        return "budget_blocked"
    if "blocked" in lowered:
        return "permission_or_source_blocked"
    return "tool_error"


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
        actions = sanitize_user_facing_actions(assessment.get("action_items_zh") or [])
        action = actions[0] if actions else f"查看「{title}」的影响与原始证据"
        recommendations.append(f"{title}: {action}")
        if len(recommendations) >= 5:
            break
    return recommendations


def _balanced_signal_list(
    assessments: list[dict[str, Any]],
    event_by_id: dict[str, dict[str, Any]],
    *,
    limit: int,
) -> list[dict[str, Any]]:
    sorted_items = sorted(
        assessments,
        key=lambda value: _to_float(value.get("impact_score")),
        reverse=True,
    )
    selected: list[dict[str, Any]] = []
    group_counts: Counter[tuple[str, str]] = Counter()
    release_dependency_count = 0
    for item in sorted_items:
        event = event_by_id.get(str(item.get("event_id")), {})
        category = str(item.get("category") or "unknown")
        source_name = str(event.get("source_name") or "unknown")
        source_type = str(event.get("source_type") or "unknown")
        key = (source_name, category)
        if category == "dependency_update" or source_type == "github_release":
            if release_dependency_count >= 3:
                continue
            release_dependency_count += 1
        if group_counts[key] >= 2:
            continue
        selected.append(item)
        group_counts[key] += 1
        if len(selected) >= limit:
            break
    if len(selected) < limit:
        for item in sorted_items:
            if item in selected:
                continue
            selected.append(item)
            if len(selected) >= limit:
                break
    return selected


def _section_rows(
    assessments: list[dict[str, Any]],
    event_by_id: dict[str, dict[str, Any]],
    *,
    categories: set[str],
    source_types: set[str] | None = None,
    limit: int,
    empty: str,
) -> str:
    rows = [
        item
        for item in sorted(
            assessments,
            key=lambda value: _to_float(value.get("impact_score")),
            reverse=True,
        )
        if str(item.get("category") or "") in categories
        or (
            source_types is not None
            and str(event_by_id.get(str(item.get("event_id")), {}).get("source_type") or "")
            in source_types
        )
    ][:limit]
    if not rows:
        return f"<tr><td colspan=\"4\">{_e(empty)}</td></tr>"
    return "\n".join(
        _signal_row(item, event_by_id.get(str(item.get("event_id")), {}))
        for item in rows
    )


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
        f"<td>{_e(_display_reason(assessment.get('reason')))}</td>"
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


def _display_reason(value: object) -> str:
    text = " ".join(str(value or "").split())
    if not text:
        return ""
    tool_debug_markers = (
        "RSS parse failed",
        "RSS request failed",
        "GitHub request failed",
        "syntax error: line",
    )
    if any(marker.lower() in text.lower() for marker in tool_debug_markers):
        return "Evidence confidence reduced due to tool errors."
    return text


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
        "No learning was applied automatically. Learning proposals are review-only "
        "and require explicit review before apply."
    )
    fallback_note = (
        "<p class=\"muted\">This run included fallback, so any learning proposal should be treated as review-only and not applied without manual inspection.</p>"
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
