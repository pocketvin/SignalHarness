"""Workflow trace collection for terminal and JSON visualization."""

from __future__ import annotations

import time
from contextlib import contextmanager
from typing import Any, Iterator

from signal_harness.signal.schemas import TraceStep


class TraceRecorder:
    """Collect duration, counts, agent identity, and failure details."""

    def __init__(self) -> None:
        self.steps: list[TraceStep] = []

    @contextmanager
    def step(
        self,
        name: str,
        *,
        agent: str | None = None,
        input_count: int | None = None,
    ) -> Iterator[dict[str, Any]]:
        started = time.perf_counter()
        state: dict[str, Any] = {
            "output_count": None,
            "detail": "",
            "failed_sources": [],
            "cache_events": [],
            "source_tasks": [],
        }
        status = "success"
        try:
            yield state
        except Exception as exc:
            status = "error"
            state["detail"] = str(exc)
            raise
        finally:
            duration_ms = max(0, round((time.perf_counter() - started) * 1000))
            self.steps.append(
                TraceStep(
                    step=name,
                    agent=agent,
                    status=status,
                    input_count=input_count,
                    output_count=(
                        int(state["output_count"])
                        if isinstance(state["output_count"], int)
                        else None
                    ),
                    duration_ms=duration_ms,
                    detail=str(state["detail"] or ""),
                    failed_sources=[
                        str(value) for value in state.get("failed_sources", [])
                    ],
                    cache_events=[
                        str(value) for value in state.get("cache_events", [])
                    ],
                    source_tasks=list(state.get("source_tasks", [])),
                )
            )
