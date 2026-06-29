"""Schema-first Agent invocation with retry, timeout, and trace output."""

from __future__ import annotations

import asyncio
import json
import re
import time
from collections.abc import Callable, Iterable
from dataclasses import replace
from typing import Any, Protocol, TypeVar

from pydantic import BaseModel

from signal_harness.agent_integration.mode import RunMode
from signal_harness.agent_integration.schemas import LearningPolicyOutput
from signal_harness.agent_integration.trace import append_llm_trace
from signal_harness.providers.adapter import AgentCall, AgentProvider
from signal_harness.runtime.tracing import TraceRecorder

OutputT = TypeVar("OutputT", bound=BaseModel)


class AgentInvocationLimits(Protocol):
    """Minimal limits consumed by AgentInvoker."""

    @property
    def max_schema_retries(self) -> int: ...

    @property
    def max_agent_call_seconds(self) -> int: ...


class AgentInvoker:
    """Invoke one Agent call while preserving SignalHarness trace semantics."""

    def __init__(
        self,
        *,
        provider: AgentProvider,
        mode: RunMode,
        trace: TraceRecorder,
        limits: AgentInvocationLimits,
    ) -> None:
        self.provider = provider
        self.mode = mode
        self.trace = trace
        self.limits = limits

    async def invoke(
        self,
        call: AgentCall,
        output_model: type[OutputT],
        fallback: Callable[[], OutputT],
        *,
        event_ids: Iterable[str],
    ) -> tuple[OutputT, int]:
        started = time.perf_counter()
        schema_valid = False
        fallback_used = False
        error: str | None = None
        schema_error: str | None = None
        retry_count = 0

        def parse_response(response: str) -> OutputT:
            return output_model.model_validate(_json_object(response))

        async def provider_text(target: AgentCall) -> str:
            try:
                return await asyncio.wait_for(
                    self.provider.complete(target),
                    timeout=self.limits.max_agent_call_seconds,
                )
            except (TimeoutError, asyncio.TimeoutError) as exc:
                raise TimeoutError(
                    f"provider_timeout after "
                    f"{self.limits.max_agent_call_seconds}s"
                ) from exc

        try:
            output = parse_response(await provider_text(call))
            schema_valid = True
        except TimeoutError as exc:
            schema_error = _short_error(exc)
            error = schema_error
            fallback_used = True
            output = fallback()
        except Exception as exc:
            schema_error = _short_error(exc)
            if self.limits.max_schema_retries <= 0:
                fallback_used = True
                output = fallback()
                error = schema_error
            else:
                retry_count = 1
                retry_call = replace(
                    call,
                    user_prompt=(
                        call.user_prompt.rstrip()
                        + "\n\n"
                        + "Your previous response failed JSON/schema validation: "
                        + schema_error
                        + ". Return exactly one valid JSON object matching the required "
                        + "schema. Do not include Markdown."
                    ),
                )
                try:
                    output = parse_response(await provider_text(retry_call))
                    schema_valid = True
                except Exception as retry_exc:
                    retry_error = _short_error(retry_exc)
                    schema_error = f"{schema_error}; retry failed: {retry_error}"
                    error = schema_error
                    fallback_used = True
                    output = fallback()
        duration_ms = max(0, round((time.perf_counter() - started) * 1000))
        source_types, requested, executed, tool_errors = _trace_tools(output)
        index = append_llm_trace(
            self.trace,
            call=call,
            provider=self.provider,
            mode=self.mode,
            input_event_id=",".join(event_ids) or "memory",
            input_count=call.input_count,
            output_count=_output_count(output),
            duration_ms=duration_ms,
            schema_valid=schema_valid,
            fallback_used=fallback_used,
            source_types_observed=source_types,
            tools_requested=requested,
            tools_executed=executed,
            tool_errors=tool_errors,
            retry_count=retry_count,
            schema_error=schema_error,
            error=error,
        )
        return output, index


def _json_object(text: str) -> dict[str, Any]:
    stripped = text.strip()
    if not stripped:
        raise ValueError("Agent response was empty")
    if stripped.startswith("```"):
        stripped = re.sub(r"^```(?:json)?\s*", "", stripped)
        stripped = re.sub(r"\s*```$", "", stripped)
    payload = json.loads(stripped)
    if not isinstance(payload, dict):
        raise ValueError("Agent response must be a JSON object")
    return payload


def _output_count(output: BaseModel) -> int:
    payload = output.model_dump(mode="json")
    for key in ("routes", "results", "tool_requests"):
        value = payload.get(key)
        if isinstance(value, list):
            return len(value)
    if isinstance(output, LearningPolicyOutput):
        return 3
    return 1


def _short_error(exc: Exception, *, limit: int = 320) -> str:
    message = re.sub(r"\s+", " ", str(exc)).strip()
    if not message:
        message = exc.__class__.__name__
    return message[:limit]


def _trace_tools(output: BaseModel) -> tuple[list[str], list[str], list[str], list[str]]:
    payload = output.model_dump(mode="json")
    source_types: list[str] = []
    requested: list[str] = []
    executed: list[str] = []
    errors: list[str] = []
    if isinstance(payload.get("source_types_observed"), list):
        source_types.extend(str(item) for item in payload["source_types_observed"])
    if isinstance(payload.get("tool_requests"), list):
        requested.extend(
            str(item.get("tool_name"))
            for item in payload["tool_requests"]
            if isinstance(item, dict) and item.get("tool_name")
        )
    for result in payload.get("results", []):
        if not isinstance(result, dict):
            continue
        source_types.extend(str(item) for item in result.get("source_types_observed", []))
        requested.extend(str(item) for item in result.get("tools_requested", []))
        executed.extend(str(item) for item in result.get("tools_executed", []))
        errors.extend(str(item) for item in result.get("tool_errors", []))
    if isinstance(payload.get("tools_requested"), list):
        requested.extend(str(item) for item in payload["tools_requested"])
    return (
        list(dict.fromkeys(source_types)),
        list(dict.fromkeys(requested)),
        list(dict.fromkeys(executed)),
        list(dict.fromkeys(errors)),
    )
