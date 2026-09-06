"""Workflow trace collection for terminal, JSON, and live visualization."""

from __future__ import annotations

import time
from collections.abc import Callable, Iterable, Iterator
from contextlib import contextmanager
from typing import Any, Literal, SupportsIndex, overload

from signal_harness.signal.schemas import TraceStep

TraceChangeKind = Literal["append", "update"]
TraceListener = Callable[[TraceChangeKind, int, TraceStep], None]


class ObservableTraceSteps(list[TraceStep]):
    """List-compatible trace storage that emits append/update notifications."""

    def __init__(self, listener: TraceListener | None = None) -> None:
        super().__init__()
        self._listener = listener

    def append(self, item: TraceStep) -> None:
        super().append(item)
        self._notify("append", len(self) - 1, item)

    @overload
    def __setitem__(self, key: SupportsIndex, value: TraceStep, /) -> None: ...

    @overload
    def __setitem__(self, key: slice, value: Iterable[TraceStep], /) -> None: ...
    def __setitem__(
        self,
        key: SupportsIndex | slice,
        value: TraceStep | Iterable[TraceStep],
        /,
    ) -> None:
        if isinstance(key, slice):
            if isinstance(value, TraceStep):
                raise TypeError("slice assignment requires an iterable of TraceStep")
            super().__setitem__(key, value)
            start, stop, step = key.indices(len(self))
            for index in range(start, stop, step):
                self._notify("update", index, self[index])
            return
        if not isinstance(value, TraceStep):
            raise TypeError("trace step assignment requires TraceStep")
        index = int(key)
        super().__setitem__(index, value)
        normalized = index if index >= 0 else len(self) + index
        self._notify("update", normalized, value)

    def _notify(self, kind: TraceChangeKind, index: int, item: TraceStep) -> None:
        if self._listener is None:
            return
        try:
            self._listener(kind, index, item)
        except Exception:
            # Observability must never break the workflow it observes.
            return


class TraceRecorder:
    """Collect duration, counts, agent identity, failure details, and live events."""

    def __init__(self, listener: TraceListener | None = None) -> None:
        self.steps: ObservableTraceSteps = ObservableTraceSteps(listener)

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
            "metadata": {},
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
                    metadata=(
                        dict(state["metadata"])
                        if isinstance(state.get("metadata"), dict)
                        else {}
                    ),
                    failed_sources=[
                        str(value) for value in state.get("failed_sources", [])
                    ],
                    cache_events=[
                        str(value) for value in state.get("cache_events", [])
                    ],
                    source_tasks=list(state.get("source_tasks", [])),
                )
            )
