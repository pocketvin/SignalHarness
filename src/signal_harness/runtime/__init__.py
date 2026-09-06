"""Runtime public surface with lazy imports to avoid tool-registry cycles."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from signal_harness.runtime.tool_executor import SignalToolExecutor
    from signal_harness.runtime.tracing import TraceRecorder

__all__ = [
    "SIGNAL_TOOL_ALLOWLIST",
    "SignalToolExecutor",
    "TraceRecorder",
    "create_signal_tool_registry",
]


def __getattr__(name: str) -> Any:
    if name == "SignalToolExecutor":
        from signal_harness.runtime.tool_executor import SignalToolExecutor

        return SignalToolExecutor
    if name == "TraceRecorder":
        from signal_harness.runtime.tracing import TraceRecorder

        return TraceRecorder
    if name in {"SIGNAL_TOOL_ALLOWLIST", "create_signal_tool_registry"}:
        from signal_harness.runtime.tool_registry import (
            SIGNAL_TOOL_ALLOWLIST,
            create_signal_tool_registry,
        )

        return {
            "SIGNAL_TOOL_ALLOWLIST": SIGNAL_TOOL_ALLOWLIST,
            "create_signal_tool_registry": create_signal_tool_registry,
        }[name]
    raise AttributeError(name)
