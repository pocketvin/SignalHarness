"""Deterministic scoring exposed as a SignalHarness tool."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from signal_harness.runtime.tools_base import BaseTool, ToolExecutionContext, ToolResult
from signal_harness.signal.scorer import score_signal
from signal_harness.signal.schemas import FeedbackRecord, SignalCategory, SignalEvent


class SignalScoreInput(BaseModel):
    event: dict[str, Any]
    project_profile: dict[str, Any]
    policy: dict[str, Any]
    seen_hashes: list[str] = Field(default_factory=list)
    feedback_history: list[dict[str, Any]] = Field(default_factory=list)
    category: SignalCategory | None = None


class SignalScoreTool(BaseTool):
    """Score a normalized signal using rules and configured weights."""

    name = "signal_score"
    description = "Calculate a 0-100 signal score and expose every weighted component."
    input_model = SignalScoreInput

    def is_read_only(self, arguments: SignalScoreInput) -> bool:
        return True

    async def execute(
        self,
        arguments: SignalScoreInput,
        context: ToolExecutionContext,
    ) -> ToolResult:
        del context
        breakdown = score_signal(
            SignalEvent.model_validate(arguments.event),
            arguments.project_profile,
            arguments.policy,
            seen_hashes=set(arguments.seen_hashes),
            feedback_history=[
                FeedbackRecord.model_validate(item) for item in arguments.feedback_history
            ],
            category=arguments.category,
        )
        return ToolResult(output=breakdown.model_dump_json())
