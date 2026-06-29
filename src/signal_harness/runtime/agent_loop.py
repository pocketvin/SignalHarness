"""Optional LLM-facing adapter over the original OpenHarness Agent Loop."""

from __future__ import annotations

from pathlib import Path
from typing import Any, AsyncIterator

from signal_harness.runtime.tool_registry import (
    SIGNAL_TOOL_ALLOWLIST,
    create_signal_tool_registry,
)

SIGNAL_AGENT_SYSTEM_PROMPT = """You are SignalHarness, a project-intelligence agent.
Use only the provided signal tools. Preserve structured evidence, never edit code,
never create issues or notifications, and never apply policy changes without
explicit user approval. Deterministic scores are authoritative; natural-language
reasoning may explain them but must not replace them."""


class SignalAgentLoop:
    """Thin SignalHarness facade around OpenHarness's QueryEngine."""

    def __init__(
        self,
        *,
        api_client: Any,
        cwd: str | Path,
        model: str,
        system_prompt: str = SIGNAL_AGENT_SYSTEM_PROMPT,
        max_turns: int = 8,
    ) -> None:
        try:
            from openharness.config.settings import PermissionSettings
            from openharness.engine.query_engine import QueryEngine
            from openharness.permissions import PermissionChecker, PermissionMode
        except ModuleNotFoundError as exc:
            raise RuntimeError(
                "SignalAgentLoop requires the optional OpenHarness runtime. "
                "SignalHarness demo/mock-agent modes do not need this integration."
            ) from exc
        root = Path(cwd).expanduser().resolve()
        self.engine = QueryEngine(
            api_client=api_client,
            tool_registry=create_signal_tool_registry(),
            permission_checker=PermissionChecker(
                PermissionSettings(
                    mode=PermissionMode.DEFAULT,
                    allowed_tools=sorted(SIGNAL_TOOL_ALLOWLIST),
                    denied_tools=[
                        "bash",
                        "write_file",
                        "edit_file",
                        "notebook_edit",
                        "web_search",
                        "web_fetch",
                        "mcp",
                    ],
                )
            ),
            cwd=root,
            model=model,
            system_prompt=system_prompt,
            max_turns=max_turns,
            tool_metadata={
                "config_dir": str(root / "configs"),
                "output_dir": str(root / "outputs"),
                "state_dir": str(root / ".signal-harness"),
            },
        )

    async def submit(self, prompt: str) -> AsyncIterator[Any]:
        """Submit one prompt to the reused OpenHarness tool-aware loop."""

        async for event in self.engine.submit_message(prompt):
            yield event
