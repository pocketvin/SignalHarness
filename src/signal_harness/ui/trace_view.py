"""Trace summary formatting."""

from __future__ import annotations

from pathlib import Path
from typing import Iterable

from rich.console import Console
from rich.table import Table

from signal_harness.utils.fs import atomic_write_text
from signal_harness.signal.schemas import TraceStep

STAGE_ALIASES = {
    "verify_evidence": "evidence",
    "analyze_impact": "impact",
    "plan_action": "action",
    "write_radar_digest": "report",
    "write_run_summary": "report",
    "write_json_outputs": "report",
}
FLOW_ORDER = (
    "load_config",
    "collect_signals",
    "normalize",
    "deduplicate",
    "noise_filter",
    "cluster_signals",
    "llm_agent_call",
    "repair_requested",
    "repair_context_evidence",
    "repair_impact",
    "repair_action",
    "repair_blocked",
    "skipped_event_audit_fallback",
    "deterministic_fallback",
    "classify",
    "evidence",
    "score",
    "impact",
    "action",
    "report",
)


def format_trace_summary(steps: Iterable[TraceStep]) -> str:
    """Return one line per trace step for logs and interview demos."""

    lines: list[str] = []
    for step in steps:
        stage = STAGE_ALIASES.get(step.step, step.step)
        counts = ""
        if step.input_count is not None or step.output_count is not None:
            counts = f" input={step.input_count} output={step.output_count}"
        line = (
            f"{stage}: {step.status} ({step.duration_ms} ms)"
            + (f" [{step.agent}]" if step.agent else "")
            + (f" mode={step.mode}" if step.mode else "")
            + (" fallback=true" if step.fallback_used else "")
            + counts
        )
        lines.append(line)
        if step.permission_checks:
            lines.append(
                "  permission_checks: " + ", ".join(step.permission_checks)
            )
        if step.error:
            lines.append(f"  error: {step.error}")
        if step.step.startswith("repair_") and step.detail:
            lines.append(f"  repair_detail: {step.detail}")
        if step.source_types_observed:
            lines.append(
                "  source_types_observed: "
                + ", ".join(step.source_types_observed)
            )
        if step.tools_requested:
            lines.append("  tools_requested: " + ", ".join(step.tools_requested))
        if step.tools_executed:
            lines.append("  tools_executed: " + ", ".join(step.tools_executed))
        if step.blocked_tools:
            lines.append("  blocked_tools: " + ", ".join(step.blocked_tools))
        if step.budget_blocked_count:
            lines.append(f"  budget_blocked_count: {step.budget_blocked_count}")
        if step.tool_errors:
            lines.append("  tool_errors: " + "; ".join(step.tool_errors))
        if step.retry_count:
            lines.append(f"  retry_count: {step.retry_count}")
        if step.schema_error:
            lines.append(f"  schema_error: {step.schema_error}")
        if step.cache_events:
            lines.append("  cache: " + ", ".join(step.cache_events))
        if step.prompt_prefix_hash:
            lines.append(f"  prompt_prefix_hash: {step.prompt_prefix_hash}")
        if (
            step.agent_name == "ContextEvidenceAgent"
            and step.output_schema == "ContextEvidenceOutput"
        ):
            lines.extend(
                [
                    "  ContextEvidenceAgent final:",
                    f"    events: {step.event_input_count or 0}",
                    f"    tool_observations: {step.tool_observation_count or 0}",
                    f"    total_inputs: {step.input_count or 0}",
                    f"    source_types: {step.source_type_count or 0}",
                    f"    tools_requested: {step.tools_requested_count or 0}",
                    f"    tools_executed: {step.tools_executed_count or 0}",
                    f"    budget_blocked: {step.budget_blocked_count or 0}",
                    f"    exit_condition: {step.exit_condition or 'unknown'}",
                ]
            )
        lines.extend(f"  failed_source: {source}" for source in step.failed_sources)
        lines.extend(
            (
                f"  source_task: {task.source_type}:{task.source_name} "
                f"status={task.status} cache_hit={task.cache_hit}"
            )
            for task in step.source_tasks
        )
    return "\n".join(lines)


def render_trace_table(
    steps: Iterable[TraceStep],
    *,
    console: Console | None = None,
) -> None:
    """Render a phase-oriented trace table with failed source diagnostics."""

    target = console or Console()
    step_list = list(steps)
    present = {STAGE_ALIASES.get(step.step, step.step) for step in step_list}
    flow = [stage for stage in FLOW_ORDER if stage in present]
    target.print(" → ".join(flow))
    table = Table(title="SignalHarness Task Trace")
    table.add_column("Stage")
    table.add_column("Status")
    table.add_column("Duration", justify="right")
    table.add_column("Input", justify="right")
    table.add_column("Output", justify="right")
    table.add_column("Agent")
    table.add_column("Kind")
    table.add_column("Fallback")
    table.add_column("Tools")
    table.add_column("Cache")
    table.add_column("Permission checks")
    for step in step_list:
        tool_parts = []
        if step.tools_requested:
            tool_parts.append("req=" + ",".join(step.tools_requested))
        if step.tools_executed:
            tool_parts.append("exec=" + ",".join(step.tools_executed))
        if step.blocked_tools:
            tool_parts.append("blocked=" + ",".join(step.blocked_tools))
        if step.budget_blocked_count:
            tool_parts.append(f"budget={step.budget_blocked_count}")
        table.add_row(
            STAGE_ALIASES.get(step.step, step.step),
            step.status,
            f"{step.duration_ms} ms",
            "" if step.input_count is None else str(step.input_count),
            "" if step.output_count is None else str(step.output_count),
            step.agent_name or step.agent or "",
            "LLM Agent" if step.step == "llm_agent_call" else "deterministic",
            "yes" if step.fallback_used else "",
            " ".join(tool_parts),
            ",".join(step.cache_events),
            ", ".join(step.permission_checks),
        )
    target.print(table)
    failed_sources = [
        source for step in step_list for source in step.failed_sources
    ]
    if failed_sources:
        target.print("[yellow]Failed sources:[/yellow]")
        for source in dict.fromkeys(failed_sources):
            target.print(f"- {source}")


def write_trace_summary(
    output_dir: str | Path,
    steps: Iterable[TraceStep],
) -> Path:
    """Write a readable Markdown trace alongside task_trace.json."""

    root = Path(output_dir).expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    step_list = list(steps)
    lines = [
        "# SignalHarness Trace Summary",
        "",
        "## Flow",
        "",
    ]
    present = {STAGE_ALIASES.get(step.step, step.step) for step in step_list}
    flow = [stage for stage in FLOW_ORDER if stage in present]
    lines.extend([" → ".join(flow), "", "## Steps", ""])
    lines.extend(
        [
            "| Stage | Status | Duration | Input | Output | Agent | Kind | Fallback | Tools | Cache | Permissions |",
            "|---|---:|---:|---:|---:|---|---|---:|---|---|---|",
        ]
    )
    for step in step_list:
        tool_parts = []
        if step.tools_requested:
            tool_parts.append("req=" + ",".join(step.tools_requested))
        if step.tools_executed:
            tool_parts.append("exec=" + ",".join(step.tools_executed))
        if step.blocked_tools:
            tool_parts.append("blocked=" + ",".join(step.blocked_tools))
        if step.budget_blocked_count:
            tool_parts.append(f"budget={step.budget_blocked_count}")
        lines.append(
            "| "
            + " | ".join(
                (
                    STAGE_ALIASES.get(step.step, step.step),
                    step.status,
                    f"{step.duration_ms} ms",
                    "" if step.input_count is None else str(step.input_count),
                    "" if step.output_count is None else str(step.output_count),
                    step.agent_name or step.agent or "",
                    "LLM Agent" if step.step == "llm_agent_call" else "deterministic",
                    "yes" if step.fallback_used else "",
                    " ".join(tool_parts),
                    ",".join(step.cache_events),
                    ", ".join(step.permission_checks),
                )
            )
            + " |"
        )
    failed_sources = list(
        dict.fromkeys(source for step in step_list for source in step.failed_sources)
    )
    if failed_sources:
        lines.extend(["", "## Failed Sources", ""])
        lines.extend(f"- {source}" for source in failed_sources)
    source_tasks = [
        task for step in step_list for task in step.source_tasks
    ]
    if source_tasks:
        lines.extend(["", "## Source Tasks", ""])
        lines.extend(
            (
                f"- `{task.source_type}:{task.source_name}`: {task.status}, "
                f"{task.output_count} outputs, {task.duration_ms} ms, "
                f"cache_hit={str(task.cache_hit).lower()}"
                + (f", error={task.error}" if task.error else "")
            )
            for task in source_tasks
        )
    prompt_steps = [step for step in step_list if step.prompt_prefix_hash]
    if prompt_steps:
        lines.extend(["", "## Prompt Context Hashes", ""])
        lines.extend(
            (
                f"- `{step.agent_name}` / `{step.output_schema}`: "
                f"prefix `{step.prompt_prefix_hash}`, "
                f"static `{step.static_context_hash}`, "
                f"dynamic `{step.dynamic_context_hash}`, "
                f"packet `{step.context_packet_version}`"
            )
            for step in prompt_steps
        )
    retry_steps = [
        step
        for step in step_list
        if step.retry_count or step.schema_error or step.fallback_used
    ]
    if retry_steps:
        lines.extend(["", "## Schema Retry and Fallback", ""])
        lines.extend(
            (
                f"- `{step.agent_name or step.agent or step.step}` / "
                f"`{step.output_schema or step.step}`: "
                f"retry_count={step.retry_count}, "
                f"schema_error={step.schema_error or 'none'}, "
                f"fallback_used={str(step.fallback_used).lower()}"
            )
            for step in retry_steps
        )
    tool_steps = [
        step
        for step in step_list
        if step.tools_requested
        or step.tools_executed
        or step.blocked_tools
        or step.budget_blocked_count
        or step.tool_observation_count
        or step.permission_checks
    ]
    if tool_steps:
        lines.extend(["", "## Tool Controls", ""])
        lines.extend(
            (
                f"- `{step.agent_name or step.agent or step.step}`: "
                f"tools_requested_count={step.tools_requested_count if step.tools_requested_count is not None else len(step.tools_requested)}, "
                f"tools_executed_count={step.tools_executed_count if step.tools_executed_count is not None else len(step.tools_executed)}, "
                f"budget_blocked_count={step.budget_blocked_count or 0}, "
                f"tool_observation_count={step.tool_observation_count or 0}, "
                f"exit_condition={step.exit_condition or 'n/a'}, "
                f"blocked_tools={','.join(step.blocked_tools) or 'none'}, "
                f"permission_checks={'; '.join(step.permission_checks) or 'none'}"
            )
            for step in tool_steps
        )
    evidence_final = next(
        (
            step
            for step in step_list
            if step.agent_name == "ContextEvidenceAgent"
            and step.output_schema == "ContextEvidenceOutput"
        ),
        None,
    )
    if evidence_final is not None:
        lines.extend(
            [
                "",
                "## ContextEvidenceAgent Final",
                "",
                f"- events: {evidence_final.event_input_count or 0}",
                (
                    "- tool_observations: "
                    f"{evidence_final.tool_observation_count or 0}"
                ),
                f"- total_inputs: {evidence_final.input_count or 0}",
                f"- source_types: {evidence_final.source_type_count or 0}",
                (
                    "- tools_requested: "
                    f"{evidence_final.tools_requested_count or 0}"
                ),
                (
                    "- tools_executed: "
                    f"{evidence_final.tools_executed_count or 0}"
                ),
                (
                    "- budget_blocked: "
                    f"{evidence_final.budget_blocked_count or 0}"
                ),
                f"- exit_condition: {evidence_final.exit_condition or 'unknown'}",
            ]
        )
    repair_steps = [
        step
        for step in step_list
        if step.step
        in {
            "repair_requested",
            "repair_context_evidence",
            "repair_impact",
            "repair_action",
            "repair_blocked",
        }
    ]
    if repair_steps:
        requested = sum(1 for step in repair_steps if step.step == "repair_requested")
        executed = sum(
            1
            for step in repair_steps
            if step.step
            in {"repair_context_evidence", "repair_impact", "repair_action"}
        )
        blocked = sum(1 for step in repair_steps if step.step == "repair_blocked")
        fallback = sum(step.fallback_used for step in repair_steps)
        event_ids = _repair_event_ids(repair_steps)
        lines.extend(
            [
                "",
                "## Agent Repair Pass",
                "",
                f"- requested: {requested}",
                f"- executed: {executed}",
                f"- blocked: {blocked}",
                f"- fallback: {fallback}",
                f"- event_ids: {', '.join(event_ids) if event_ids else 'none'}",
                "",
            ]
        )
        lines.extend(
            (
                f"- `{step.step}`: {step.detail or 'no detail'}"
                for step in repair_steps
            )
        )
    else:
        lines.extend(
            [
                "",
                "## Agent Repair Pass",
                "",
                "No repair pass was triggered.",
            ]
        )
    skipped_audit_steps = [
        step
        for step in step_list
        if step.step == "skipped_event_audit_fallback"
    ]
    if skipped_audit_steps:
        lines.extend(
            [
                "",
                "## Skipped Event Audit Completion",
                "",
                (
                    "Supervisor-routed skips are completed with deterministic fallback "
                    "only so every event retains a full audit assessment. This is not "
                    "downstream LLM Agent execution."
                ),
                "",
            ]
        )
        lines.extend(f"- {step.detail}" for step in skipped_audit_steps)
    path = root / "trace_summary.md"
    atomic_write_text(path, "\n".join(lines).rstrip() + "\n")
    return path


def _repair_event_ids(steps: Iterable[TraceStep]) -> list[str]:
    event_ids: list[str] = []
    for step in steps:
        marker = "event_ids="
        if marker not in step.detail:
            continue
        tail = step.detail.split(marker, 1)[1]
        value = tail.split(";", 1)[0]
        event_ids.extend(item.strip() for item in value.split(",") if item.strip())
    return list(dict.fromkeys(event_ids))
