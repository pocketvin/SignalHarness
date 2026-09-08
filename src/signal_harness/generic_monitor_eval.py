"""Live-provider baseline for a generic LLM project-change monitor."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field

from signal_harness.agent_integration.invoker import AgentInvoker
from signal_harness.agent_integration.mode import RunMode
from signal_harness.agent_integration.runner import AgentLoopLimits
from signal_harness.evals import (
    RegressionEvalSummary,
    evaluate_regression_suite,
    load_regression_suite,
)
from signal_harness.providers.adapter import AgentCall, AgentProvider
from signal_harness.runtime.tracing import TraceRecorder
from signal_harness.signal.normalizer import normalize_event
from signal_harness.signal.schemas import (
    SignalAssessment,
    SignalCategory,
    SignalDecision,
    SignalEvent,
)
from signal_harness.utils.fs import atomic_write_text

GENERIC_MONITOR_PROMPT_VERSION = "generic-monitor-v1"
GENERIC_MONITOR_CONTEXT_VERSION = "compact-project-brief-v1"


class GenericMonitorItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    event_id: str = Field(min_length=1)
    category: SignalCategory
    decision: SignalDecision
    reason: str = ""


class GenericMonitorOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    results: list[GenericMonitorItem]


class GenericMonitorEvalSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    baseline_type: str = "generic-llm-monitor"
    prompt_version: str = GENERIC_MONITOR_PROMPT_VERSION
    context_version: str = GENERIC_MONITOR_CONTEXT_VERSION
    event_fingerprint: str
    event_count: int = Field(ge=0)
    project_brief_hash: str
    project_brief_chars: int = Field(ge=0)
    prompt_chars: int = Field(ge=0)
    provider: str
    model: str
    model_profile: str
    schema_valid: bool
    fallback_used: bool
    comparison_valid: bool
    llm_latency_ms: int = Field(ge=0)
    total_tokens: int = Field(ge=0)
    estimated_cost_usd: float = Field(ge=0)
    regression: RegressionEvalSummary


def build_compact_project_brief(profile: dict[str, Any]) -> str:
    """Approximate what a user could cheaply tell a general monitoring task."""

    name = str(profile.get("project_name") or profile.get("name") or "project").strip()
    goal = str(profile.get("goal") or profile.get("purpose") or "").strip()

    def values(key: str, *, limit: int = 8) -> list[str]:
        raw = profile.get(key, [])
        if not isinstance(raw, list):
            return []
        return [str(item).strip() for item in raw if str(item).strip()][:limit]

    parts = [f"Project: {name}."]
    if goal:
        parts.append(f"Goal: {goal}")
    stack = values("tech_stack")
    if stack:
        parts.append("Stack: " + ", ".join(stack) + ".")
    ecosystem = values("monitored_ecosystem")
    if ecosystem:
        parts.append("Watch closely: " + ", ".join(ecosystem) + ".")
    focus = values("focus_keywords")
    if focus:
        parts.append("Important topics: " + ", ".join(focus) + ".")
    ignore = values("ignore_keywords")
    if ignore:
        parts.append("Usually ignore: " + ", ".join(ignore) + ".")
    return " ".join(parts)


def load_compact_project_brief(profile_path: Path) -> str:
    payload = yaml.safe_load(profile_path.read_text(encoding="utf-8")) or {}
    if not isinstance(payload, dict):
        raise ValueError("project profile must be a mapping")
    return build_compact_project_brief(payload)


def _event_payload(event: SignalEvent) -> dict[str, Any]:
    return {
        "event_id": event.event_id,
        "source_type": event.source_type,
        "source_name": event.source_name,
        "title": event.title,
        "content": event.content,
        "url": event.url,
        "change_kind": event.change_kind,
        "current_version": event.current_version,
        "previous_version": event.previous_version,
    }


def _event_fingerprint(events: list[SignalEvent]) -> str:
    raw = json.dumps(
        [_event_payload(event) for event in events],
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def build_generic_monitor_call(
    *,
    events: list[SignalEvent],
    project_brief: str,
) -> AgentCall:
    categories = ", ".join(item.value for item in SignalCategory)
    decisions = ", ".join(item.value for item in SignalDecision)
    system_prompt = (
        "You are a generic AI monitoring assistant. Decide which external changes matter "
        "to the user's software project using only the compact project brief and event text "
        "provided in this request. You have no project database, dependency lockfile facts, "
        "history, revision ledger, coverage state, tools, or hidden memory. "
        "Return exactly one JSON object and no Markdown. The object must contain a results "
        "array with exactly one item for every event_id. Each item must contain event_id, "
        f"category, decision, and reason. Allowed categories: {categories}. "
        f"Allowed decisions: {decisions}. Use alert/action_required only when the change is "
        "material enough that the user should be interrupted or act soon; save for relevant "
        "but non-urgent changes; ignore for noise or weak relevance."
    )
    user_prompt = json.dumps(
        {
            "project_brief": project_brief,
            "events": [_event_payload(event) for event in events],
        },
        indent=2,
        ensure_ascii=False,
    )
    return AgentCall(
        agent_name="GenericMonitorBaseline",
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        prompt_version=GENERIC_MONITOR_PROMPT_VERSION,
        output_schema=GenericMonitorOutput.__name__,
        input_payload={
            "project_brief": project_brief,
            "events": [_event_payload(event) for event in events],
        },
        input_count=len(events),
        context_packet_version=GENERIC_MONITOR_CONTEXT_VERSION,
    )


def _as_assessments(output: GenericMonitorOutput) -> list[SignalAssessment]:
    return [
        SignalAssessment(
            event_id=item.event_id,
            category=item.category,
            relevance_score=0,
            impact_score=0,
            confidence=0.5,
            reason=item.reason,
            decision=item.decision,
        )
        for item in output.results
    ]


async def run_generic_monitor_eval(
    *,
    provider: AgentProvider,
    mode: RunMode,
    fixture: Path,
    expectations: Path,
    project_profile: Path,
    max_events: int = 80,
) -> GenericMonitorEvalSummary:
    """Run one generic-monitor LLM call without SignalHarness project-state advantages."""

    raw_events = json.loads(fixture.read_text(encoding="utf-8"))
    if not isinstance(raw_events, list):
        raise ValueError("generic monitor fixture must be a JSON list")
    events = [normalize_event(item) for item in raw_events]
    if len(events) > max_events:
        raise ValueError(
            f"generic monitor corpus has {len(events)} events; max_events is {max_events}"
        )
    project_brief = load_compact_project_brief(project_profile)
    call = build_generic_monitor_call(events=events, project_brief=project_brief)
    trace = TraceRecorder()
    invoker = AgentInvoker(
        provider=provider,
        mode=mode,
        trace=trace,
        limits=AgentLoopLimits(),
    )
    output, trace_index = await invoker.invoke(
        call,
        GenericMonitorOutput,
        lambda: GenericMonitorOutput(results=[]),
        event_ids=[event.event_id for event in events],
    )
    step = trace.steps[trace_index]
    expected_ids = {event.event_id for event in events}
    actual_ids = {item.event_id for item in output.results}
    comparison_valid = (
        step.schema_valid is True
        and not step.fallback_used
        and actual_ids == expected_ids
        and len(output.results) == len(events)
    )
    regression = evaluate_regression_suite(
        assessments=_as_assessments(output),
        suite=load_regression_suite(expectations),
    )
    brief_hash = hashlib.sha256(project_brief.encode("utf-8")).hexdigest()
    return GenericMonitorEvalSummary(
        event_fingerprint=_event_fingerprint(events),
        event_count=len(events),
        project_brief_hash=brief_hash,
        project_brief_chars=len(project_brief),
        prompt_chars=len(call.system_prompt) + len(call.user_prompt),
        provider=str(step.provider or provider.name),
        model=str(step.model or provider.model),
        model_profile=str(step.model_profile or ""),
        schema_valid=step.schema_valid is True,
        fallback_used=step.fallback_used,
        comparison_valid=comparison_valid,
        llm_latency_ms=step.duration_ms,
        total_tokens=step.total_tokens or 0,
        estimated_cost_usd=step.estimated_cost_usd or 0.0,
        regression=regression,
    )


def write_generic_monitor_eval_summary(
    output_dir: Path,
    summary: GenericMonitorEvalSummary,
) -> dict[str, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / "generic_monitor_eval_summary.json"
    md_path = output_dir / "generic_monitor_eval_summary.md"
    atomic_write_text(
        json_path,
        json.dumps(summary.model_dump(mode="json"), indent=2, ensure_ascii=False) + "\n",
    )
    regression = summary.regression
    lines = [
        "# Generic LLM Monitor Baseline",
        "",
        f"**Comparison valid:** {'yes' if summary.comparison_valid else 'no'}",
        f"**Provider/model:** {summary.provider} / {summary.model}",
        f"**Events:** {summary.event_count}",
        f"**Prompt chars:** {summary.prompt_chars}",
        f"**Tokens:** {summary.total_tokens}",
        f"**Estimated cost USD:** {summary.estimated_cost_usd:.6f}",
        "",
        "## Quality on the shared labelled corpus",
        "",
        f"- decision_accuracy: {regression.decision_accuracy:.4f}",
        f"- category_accuracy: {regression.category_accuracy:.4f}",
        f"- priority_precision: {regression.priority_precision:.4f}",
        f"- priority_recall: {regression.priority_recall:.4f}",
        f"- labelled-suite gate: {'PASS' if regression.passed else 'FAIL'}",
        "",
        "This baseline intentionally receives no Change Ledger, revisions, coverage/checkpoints, "
        "dependency-version evidence, tools, or historical ProjectImpact state. It measures a "
        "generic one-pass LLM monitoring contract, not the full ChatGPT Scheduled Tasks product.",
        "",
    ]
    atomic_write_text(md_path, "\n".join(lines))
    return {"json": json_path, "markdown": md_path}
