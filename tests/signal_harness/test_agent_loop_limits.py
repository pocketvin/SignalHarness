from __future__ import annotations

import asyncio
from pathlib import Path

from signal_harness.agent_integration.mode import RunMode
from signal_harness.agent_integration.runner import AgentLoopLimits
from signal_harness.providers.adapter import AgentCall
from signal_harness.runtime.workflow import SignalHarnessWorkflow


class SlowProvider:
    name = "slow-provider"
    model = "slow-model"

    async def complete(self, call: AgentCall) -> str:
        await asyncio.sleep(10)
        return "{}"

    async def close(self) -> None:
        return None


def test_agent_provider_timeout_falls_back_without_crashing(
    project_root: Path,
    tmp_path: Path,
) -> None:
    workflow = SignalHarnessWorkflow(
        cwd=project_root,
        output_dir=tmp_path / "outputs",
        state_dir=tmp_path / "state",
        mode=RunMode.MOCK_AGENT,
        provider=SlowProvider(),
        agent_loop_limits=AgentLoopLimits(max_agent_call_seconds=0),
    )

    result = asyncio.run(
        workflow.scan(
            fixture=project_root / "examples/signal_harness/sample_events.json"
        )
    )

    assert result.assessments
    timeout_steps = [
        step
        for step in result.trace.steps
        if step.step == "llm_agent_call" and step.fallback_used
    ]
    assert timeout_steps
    assert any("provider_timeout" in (step.schema_error or "") for step in timeout_steps)
    assert any("provider_timeout" in (step.error or "") for step in timeout_steps)


def test_agent_team_run_timeout_falls_back_without_hanging(
    project_root: Path,
    tmp_path: Path,
) -> None:
    workflow = SignalHarnessWorkflow(
        cwd=project_root,
        output_dir=tmp_path / "outputs",
        state_dir=tmp_path / "state",
        mode=RunMode.MOCK_AGENT,
        provider=SlowProvider(),
        agent_loop_limits=AgentLoopLimits(
            max_agent_call_seconds=30,
            max_run_seconds=0,
        ),
    )

    result = asyncio.run(
        workflow.scan(
            fixture=project_root / "examples/signal_harness/sample_events.json"
        )
    )

    assert result.assessments
    timeout_step = next(
        step for step in result.trace.steps if step.step == "agent_team_run_timeout"
    )
    assert timeout_step.fallback_used is True
    assert "agent_team_run_timeout" in (timeout_step.error or "")
    assert any(
        step.step == "deterministic_fallback" and step.fallback_used is False
        for step in result.trace.steps
    )
