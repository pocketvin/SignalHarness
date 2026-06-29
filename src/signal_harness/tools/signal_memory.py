"""Configuration and feedback memory access through an OpenHarness tool."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, model_validator

from signal_harness.runtime.tools_base import BaseTool, ToolExecutionContext, ToolResult
from signal_harness.signal.feedback import (
    load_feedback_history,
    save_feedback,
    save_policy_proposal,
)
from signal_harness.signal.policy import load_signal_policy, load_yaml_mapping
from signal_harness.signal.schemas import FeedbackRecord, PolicyUpdateProposal


class SignalMemoryInput(BaseModel):
    action: Literal[
        "load_project_profile",
        "load_signal_policy",
        "load_watchlist",
        "save_feedback",
        "load_feedback_history",
        "save_policy_proposal",
    ]
    record: dict[str, Any] | None = None
    proposal: dict[str, Any] | None = None

    @model_validator(mode="after")
    def _validate_payload(self) -> SignalMemoryInput:
        if self.action == "save_feedback" and self.record is None:
            raise ValueError("record is required")
        if self.action == "save_policy_proposal" and self.proposal is None:
            raise ValueError("proposal is required")
        return self


class SignalMemoryTool(BaseTool):
    """Load versioned config and write readable local signal memory."""

    name = "signal_memory"
    description = "Load SignalHarness config or persist feedback/policy proposals."
    input_model = SignalMemoryInput

    def is_read_only(self, arguments: SignalMemoryInput) -> bool:
        return arguments.action.startswith("load_")

    async def execute(
        self,
        arguments: SignalMemoryInput,
        context: ToolExecutionContext,
    ) -> ToolResult:
        config_dir = Path(context.metadata.get("config_dir") or context.cwd / "configs").resolve()
        state_dir = Path(
            context.metadata.get("state_dir") or context.cwd / ".signal-harness"
        ).resolve()
        payload: object
        if arguments.action == "load_project_profile":
            payload = load_yaml_mapping(config_dir / "project_profile.yaml")
        elif arguments.action == "load_signal_policy":
            payload = load_signal_policy(config_dir / "signal_policy.yaml")
        elif arguments.action == "load_watchlist":
            payload = load_yaml_mapping(config_dir / "watchlist.yaml")
        elif arguments.action == "load_feedback_history":
            payload = [
                item.model_dump(mode="json")
                for item in load_feedback_history(state_dir / "feedback_memory.json")
            ]
        elif arguments.action == "save_feedback":
            assert arguments.record is not None
            record = FeedbackRecord.model_validate(arguments.record)
            path = save_feedback(state_dir / "feedback_memory.json", record)
            return ToolResult(output=str(path), metadata={"path": str(path)})
        else:
            assert arguments.proposal is not None
            proposal = PolicyUpdateProposal.model_validate(arguments.proposal)
            path = save_policy_proposal(
                state_dir / "signal_policy_update_proposal.json",
                proposal,
            )
            return ToolResult(output=str(path), metadata={"path": str(path)})
        return ToolResult(output=json.dumps(payload, ensure_ascii=False))
