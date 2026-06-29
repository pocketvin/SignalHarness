"""SignalHarness command-line interface."""

from __future__ import annotations

import asyncio
import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any, cast

import typer

from signal_harness.utils.fs import atomic_write_text
from signal_harness.agent_integration.mode import RunMode
from signal_harness.agent_integration.runner import LLMAgentTeamRunner
from signal_harness.agent_integration.schemas import LearningPolicyOutput
from signal_harness.agent_team.learning_policy import LearningPolicyAgent
from signal_harness.evals import build_model_eval_summary, write_model_eval_summary
from signal_harness.memory import FeedbackMemory, MemoryBundle
from signal_harness.memory.replay import evaluate_policy_replay
from signal_harness.providers.adapter import AgentProvider
from signal_harness.providers.factory import provider_from_env
from signal_harness.providers.mock_provider import MockProvider
from signal_harness.runtime.permissions import SignalPermissionGuard
from signal_harness.runtime.tracing import TraceRecorder
from signal_harness.runtime.workflow import SignalHarnessWorkflow
from signal_harness.signal.feedback import (
    create_feedback_record,
    generate_policy_proposal,
    load_feedback_history,
    save_policy_proposal,
)
from signal_harness.signal.policy import (
    apply_policy_proposal,
    load_signal_policy,
    render_policy_diff,
)
from signal_harness.signal.schemas import (
    FeedbackLabel,
    PolicyUpdateProposal,
    SignalAssessment,
    SignalEvent,
    TraceStep,
)
from signal_harness.ui.terminal_view import render_assessment_table
from signal_harness.ui.dashboard import write_dashboard
from signal_harness.ui.digest import DigestPeriod, write_digest
from signal_harness.ui.trace_view import render_trace_table, write_trace_summary

app = typer.Typer(
    name="signal-harness",
    help="Project-centric multi-agent signal intelligence.",
    no_args_is_help=True,
)


def _resolve(root: Path, value: Path) -> Path:
    return value.expanduser().resolve() if value.is_absolute() else (root / value).resolve()


def _load_outputs(output_dir: Path) -> tuple[list[SignalEvent], list[SignalAssessment]]:
    signals_path = output_dir / "signals.json"
    assessments_path = output_dir / "impact_scores.json"
    if not signals_path.exists() or not assessments_path.exists():
        raise typer.BadParameter("No scan output found. Run `signal-harness scan` first.")
    signals = [
        SignalEvent.model_validate(item)
        for item in json.loads(signals_path.read_text(encoding="utf-8"))
    ]
    assessments = [
        SignalAssessment.model_validate(item)
        for item in json.loads(assessments_path.read_text(encoding="utf-8"))
    ]
    return signals, assessments


def _require_agent_key(mode: RunMode) -> None:
    if mode is RunMode.AGENT and not os.environ.get("LLM_API_KEY", "").strip():
        typer.echo(
            "agent mode requires LLM_API_KEY. "
            "Use --mode demo or --mode mock-agent for offline execution.",
            err=True,
        )
        raise typer.Exit(code=2)


def _provider_for_mode(
    mode: RunMode,
    *,
    config_dir: Path | None = None,
) -> AgentProvider:
    _require_agent_key(mode)
    if mode is RunMode.MOCK_AGENT:
        return MockProvider()
    if mode is RunMode.AGENT:
        return provider_from_env(mode, config_dir=config_dir)
    raise ValueError("demo mode does not use an LLM provider")


async def _run_learning_with_provider(
    *,
    mode: RunMode,
    snapshot: dict[str, Any],
    trace: TraceRecorder,
    config_dir: Path | None = None,
) -> LearningPolicyOutput:
    provider = _provider_for_mode(mode, config_dir=config_dir)
    try:
        return await LLMAgentTeamRunner(
            provider=provider,
            mode=mode,
            trace=trace,
        ).run_learning(snapshot)
    finally:
        await provider.close()


def _write_json(path: Path, payload: object) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(
        path,
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
    )
    return path


def _save_learning_artifacts(
    state: Path,
    output: Path,
    *,
    proposal: PolicyUpdateProposal,
    skill_proposal: str,
    watchlist_proposal: dict[str, object],
    replay: dict[str, object],
) -> None:
    save_policy_proposal(state / "policy_update_proposal.json", proposal)
    save_policy_proposal(state / "signal_policy_update_proposal.json", proposal)
    state.mkdir(parents=True, exist_ok=True)
    atomic_write_text(
        state / "skill_update_proposal.md",
        skill_proposal.rstrip() + "\n",
    )
    _write_json(state / "watchlist_update_proposal.json", watchlist_proposal)
    _write_json(state / "replay_evaluation.json", replay)
    output.mkdir(parents=True, exist_ok=True)
    save_policy_proposal(
        output / "latest_policy_update_proposal.json",
        proposal,
    )
    atomic_write_text(
        output / "latest_skill_update_proposal.md",
        skill_proposal.rstrip() + "\n",
    )
    _write_json(
        output / "latest_watchlist_update_proposal.json",
        watchlist_proposal,
    )
    _write_json(output / "latest_replay_evaluation.json", replay)


def parse_since(value: str | None) -> datetime | None:
    """Parse supported date and ISO-8601 datetime forms, including trailing Z."""

    if value is None:
        return None
    normalized = value.strip()
    if not normalized:
        raise typer.BadParameter("--since cannot be empty")
    if normalized.endswith(("Z", "z")):
        normalized = normalized[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise typer.BadParameter(
            "Invalid --since value. Use YYYY-MM-DD or an ISO-8601 datetime, "
            "for example 2026-06-24T00:00:00Z."
        ) from exc


@app.command()
def scan(
    fixture: Path | None = typer.Option(None, "--fixture", help="Local JSON event fixture"),
    since: str | None = typer.Option(None, "--since", help="Collect events after ISO time"),
    cwd: Path = typer.Option(Path.cwd(), "--cwd", hidden=True),
    config_dir: Path = typer.Option(Path("configs"), "--config-dir"),
    output_dir: Path = typer.Option(Path("outputs"), "--output-dir"),
    state_dir: Path = typer.Option(Path(".signal-harness"), "--state-dir"),
    mode: RunMode = typer.Option(
        RunMode.DEMO,
        "--mode",
        help="demo, mock-agent, or agent",
    ),
) -> None:
    """Collect, normalize, assess, trace, and report project signals."""

    root = cwd.expanduser().resolve()
    _require_agent_key(mode)
    workflow = SignalHarnessWorkflow(
        cwd=root,
        config_dir=config_dir,
        output_dir=output_dir,
        state_dir=state_dir,
        mode=mode,
    )
    result = asyncio.run(workflow.scan(fixture=fixture, since=parse_since(since)))
    render_assessment_table(result.signals, result.assessments)
    typer.echo(f"Generated SignalHarness outputs in {result.output_dir}")


@app.command()
def report(
    cwd: Path = typer.Option(Path.cwd(), "--cwd", hidden=True),
    output_dir: Path = typer.Option(Path("outputs"), "--output-dir"),
) -> None:
    """Render the latest structured scan results."""

    root = cwd.expanduser().resolve()
    resolved_output = _resolve(root, output_dir)
    signals, assessments = _load_outputs(resolved_output)
    render_assessment_table(signals, assessments)
    digest = resolved_output / "radar_digest.md"
    typer.echo(f"Radar digest: {digest}")


@app.command()
def trace(
    cwd: Path = typer.Option(Path.cwd(), "--cwd", hidden=True),
    output_dir: Path = typer.Option(Path("outputs"), "--output-dir"),
) -> None:
    """Render the latest task trace and write outputs/trace_summary.md."""

    root = cwd.expanduser().resolve()
    resolved_output = _resolve(root, output_dir)
    trace_path = resolved_output / "task_trace.json"
    if not trace_path.exists():
        raise typer.BadParameter("No task trace found. Run `signal-harness scan` first.")
    from signal_harness.signal.schemas import TraceStep

    payload = json.loads(trace_path.read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        raise typer.BadParameter("task_trace.json must contain a JSON list")
    steps = [TraceStep.model_validate(item) for item in payload]
    render_trace_table(steps)
    summary_path = write_trace_summary(resolved_output, steps)
    typer.echo(f"Trace summary: {summary_path}")


@app.command()
def dashboard(
    cwd: Path = typer.Option(Path.cwd(), "--cwd", hidden=True),
    output_dir: Path = typer.Option(Path("outputs"), "--output-dir"),
) -> None:
    """Write a static local HTML dashboard from the latest outputs."""

    root = cwd.expanduser().resolve()
    path = write_dashboard(_resolve(root, output_dir))
    typer.echo(f"Dashboard: {path}")


@app.command()
def digest(
    period: str = typer.Option("daily", "--period", help="daily or weekly"),
    cwd: Path = typer.Option(Path.cwd(), "--cwd", hidden=True),
    output_dir: Path = typer.Option(Path("outputs"), "--output-dir"),
) -> None:
    """Write a local daily or weekly Markdown digest."""

    normalized = period.strip().lower()
    if normalized not in {"daily", "weekly"}:
        raise typer.BadParameter("--period must be daily or weekly")
    root = cwd.expanduser().resolve()
    path = write_digest(
        _resolve(root, output_dir),
        period=cast(DigestPeriod, normalized),
    )
    typer.echo(f"Digest: {path}")


@app.command("model-eval")
def model_eval(
    fixture: Path = typer.Option(
        Path("examples/signal_harness/sample_events.json"),
        "--fixture",
        help="Local JSON event fixture",
    ),
    mode: RunMode = typer.Option(
        RunMode.MOCK_AGENT,
        "--mode",
        help="demo, mock-agent, or agent",
    ),
    profile: str | None = typer.Option(
        None,
        "--profile",
        help="Model profile name or path, for example kimi or qwen",
    ),
    runs: int = typer.Option(1, "--runs", min=1),
    cwd: Path = typer.Option(Path.cwd(), "--cwd", hidden=True),
    config_dir: Path = typer.Option(Path("configs"), "--config-dir"),
    output_dir: Path = typer.Option(Path("outputs"), "--output-dir"),
    state_dir: Path = typer.Option(Path(".signal-harness/model-eval"), "--state-dir"),
) -> None:
    """Run a lightweight local model evaluation with uniform Harness metrics."""

    root = cwd.expanduser().resolve()
    _require_agent_key(mode)
    resolved_output = _resolve(root, output_dir)
    resolved_state = _resolve(root, state_dir)
    previous_profile = os.environ.get("LLM_MODEL_PROFILE")
    if profile:
        os.environ["LLM_MODEL_PROFILE"] = profile
    try:
        assessments: list[SignalAssessment] = []
        trace_steps: list[TraceStep] = []
        for _ in range(runs):
            workflow = SignalHarnessWorkflow(
                cwd=root,
                config_dir=config_dir,
                output_dir=resolved_output,
                state_dir=resolved_state,
                mode=mode,
            )
            result = asyncio.run(
                workflow.scan(
                    fixture=_resolve(root, fixture),
                )
            )
            assessments.extend(result.assessments)
            trace_steps.extend(result.trace.steps)
    finally:
        if profile:
            if previous_profile is None:
                os.environ.pop("LLM_MODEL_PROFILE", None)
            else:
                os.environ["LLM_MODEL_PROFILE"] = previous_profile

    llm_models = [step.model for step in trace_steps if step.model]
    provider = (
        "demo-deterministic"
        if mode is RunMode.DEMO
        else "mock-provider"
        if mode is RunMode.MOCK_AGENT
        else os.environ.get("LLM_PROVIDER", "openai_compatible")
    )
    summary = build_model_eval_summary(
        assessments=assessments,
        trace=trace_steps,
        runs=runs,
        provider=provider,
        model=llm_models[0] if llm_models else "deterministic",
        model_profile=(
            profile
            or os.environ.get("LLM_MODEL_PROFILE")
            or ("mock-agent" if mode is RunMode.MOCK_AGENT else "openai_gpt4o_mini")
        ),
    )
    paths = write_model_eval_summary(resolved_output, summary)
    typer.echo(f"Model eval JSON: {paths['json']}")
    typer.echo(f"Model eval Markdown: {paths['markdown']}")


@app.command()
def feedback(
    signal_id: str = typer.Option(..., "--signal-id"),
    label: FeedbackLabel = typer.Option(..., "--label"),
    note: str = typer.Option("", "--note"),
    cwd: Path = typer.Option(Path.cwd(), "--cwd", hidden=True),
    config_dir: Path = typer.Option(Path("configs"), "--config-dir"),
    output_dir: Path = typer.Option(Path("outputs"), "--output-dir"),
    state_dir: Path = typer.Option(Path(".signal-harness"), "--state-dir"),
) -> None:
    """Record feedback and generate a review-only policy proposal."""

    root = cwd.expanduser().resolve()
    signals, assessments = _load_outputs(_resolve(root, output_dir))
    known_ids = {item.event_id for item in signals} | {item.event_id for item in assessments}
    if signal_id not in known_ids and label is not FeedbackLabel.MISSED_SIGNAL:
        raise typer.BadParameter(f"Unknown signal ID: {signal_id}")
    policy = load_signal_policy(_resolve(root, config_dir) / "signal_policy.yaml")
    guard = SignalPermissionGuard(policy)
    guard.require("save_feedback")
    guard.require("save_policy_proposal")
    state = _resolve(root, state_dir)
    record = create_feedback_record(signal_id, label, note)
    FeedbackMemory(state / "feedback_memory.json").append(record)
    proposal = generate_policy_proposal(
        load_feedback_history(state / "feedback_memory.json"),
        policy,
    )
    save_policy_proposal(state / "policy_update_proposal.json", proposal)
    save_policy_proposal(state / "signal_policy_update_proposal.json", proposal)
    typer.echo(f"Saved feedback: {record.feedback.value} for {record.event_id}")
    typer.echo(f"Generated proposal: {proposal.proposal_id}")
    typer.echo("Policy was not modified.")


@app.command()
def calibrate(
    apply: bool = typer.Option(False, "--apply", help="Apply after explicit confirmation"),
    yes: bool = typer.Option(False, "--yes", help="Confirm policy replacement non-interactively"),
    cwd: Path = typer.Option(Path.cwd(), "--cwd", hidden=True),
    config_dir: Path = typer.Option(Path("configs"), "--config-dir"),
    output_dir: Path = typer.Option(Path("outputs"), "--output-dir"),
    state_dir: Path = typer.Option(Path(".signal-harness"), "--state-dir"),
    mode: RunMode = typer.Option(
        RunMode.DEMO,
        "--mode",
        help="demo, mock-agent, or agent",
    ),
) -> None:
    """Analyze feedback and propose, optionally approve, a policy update."""

    root = cwd.expanduser().resolve()
    config = _resolve(root, config_dir)
    state = _resolve(root, state_dir)
    policy_path = config / "signal_policy.yaml"
    policy = load_signal_policy(policy_path)
    bundle = MemoryBundle.from_paths(config_dir=config, state_dir=state)
    snapshot = bundle.snapshot()
    if mode is RunMode.DEMO:
        learning = LearningPolicyAgent().fallback(snapshot)
        calibration_trace = TraceRecorder()
    else:
        calibration_trace = TraceRecorder()
        learning = asyncio.run(
            _run_learning_with_provider(
                mode=mode,
                snapshot=snapshot,
                trace=calibration_trace,
                config_dir=config,
            )
        )
    proposal = learning.policy_update_proposal
    events: list[SignalEvent] = []
    resolved_outputs = _resolve(root, output_dir)
    if (resolved_outputs / "signals.json").exists():
        events, _ = _load_outputs(resolved_outputs)
    replay = evaluate_policy_replay(
        events,
        bundle.feedback.load(),
        project_profile=dict(snapshot["project_memory"]["project_profile"]),
        old_policy=policy,
        proposed_policy=proposal.new_policy,
    )
    _save_learning_artifacts(
        state,
        resolved_outputs,
        proposal=proposal,
        skill_proposal=learning.skill_update_proposal,
        watchlist_proposal=learning.watchlist_update_proposal,
        replay=replay.model_dump(mode="json"),
    )
    if calibration_trace.steps:
        _write_json(
            state / "calibration_trace.json",
            [step.model_dump(mode="json") for step in calibration_trace.steps],
        )
    typer.echo(f"Proposal: {proposal.proposal_id}")
    typer.echo(render_policy_diff(proposal.old_policy, proposal.new_policy))
    typer.echo(f"Replay recommendation: {replay.recommendation}")
    if not apply:
        typer.echo("Review the proposal; rerun with --apply to request policy replacement.")
        return
    confirmed = yes or typer.confirm("Apply this proposal to signal_policy.yaml?")
    SignalPermissionGuard(policy).require(
        "modify_signal_policy",
        confirmed=confirmed,
    )
    apply_policy_proposal(policy_path, proposal.new_policy, approved=confirmed)
    typer.echo(f"Applied policy proposal to {policy_path}")


if __name__ == "__main__":
    app()
