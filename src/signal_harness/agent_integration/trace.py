"""LLM Agent trace helpers."""

from __future__ import annotations

from signal_harness.agent_integration.mode import RunMode
from signal_harness.providers.adapter import AgentCall, AgentProvider
from signal_harness.runtime.tracing import TraceRecorder
from signal_harness.signal.schemas import TraceStep


def append_llm_trace(
    recorder: TraceRecorder,
    *,
    call: AgentCall,
    provider: AgentProvider,
    mode: RunMode,
    input_event_id: str,
    input_count: int,
    output_count: int,
    duration_ms: int,
    schema_valid: bool,
    fallback_used: bool,
    tools_requested: list[str],
    source_types_observed: list[str] | None = None,
    tools_executed: list[str] | None = None,
    tool_errors: list[str] | None = None,
    blocked_tools: list[str] | None = None,
    permission_checks: list[str] | None = None,
    error: str | None = None,
) -> int:
    """Append one complete LLM invocation record and return its index."""

    recorder.steps.append(
        TraceStep(
            step="llm_agent_call",
            agent=call.agent_name,
            agent_name=call.agent_name,
            mode=mode.value,
            model=provider.model,
            prompt_version=call.prompt_version,
            input_event_id=input_event_id,
            output_schema=call.output_schema,
            schema_valid=schema_valid,
            fallback_used=fallback_used,
            duration_ms=duration_ms,
            source_types_observed=source_types_observed or [],
            tools_requested=tools_requested,
            tools_executed=tools_executed or [],
            tool_errors=tool_errors or [],
            blocked_tools=blocked_tools or [],
            permission_checks=permission_checks or [],
            prompt_prefix_hash=call.prompt_prefix_hash or None,
            static_context_hash=call.static_context_hash or None,
            dynamic_context_hash=call.dynamic_context_hash or None,
            context_packet_version=call.context_packet_version or None,
            cache_strategy=call.cache_strategy,
            error=error,
            status="success",
            input_count=input_count,
            output_count=output_count,
            detail=(
                f"Schema fallback used: {error}"
                if fallback_used and error
                else "Structured LLM Agent output accepted."
            ),
        )
    )
    return len(recorder.steps) - 1
