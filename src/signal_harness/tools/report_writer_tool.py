"""Report generation exposed through the OpenHarness tool protocol."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field

from signal_harness.runtime.tools_base import BaseTool, ToolExecutionContext, ToolResult
from signal_harness.signal.schemas import SignalAssessment, SignalEvent, SourceTask, TraceStep
from signal_harness.tools.report_writer import (
    write_json_outputs,
    write_radar_digest,
    write_run_summary,
)


class ReportWriterInput(BaseModel):
    action: Literal["write_radar_digest", "write_json_outputs", "write_run_summary"]
    signals: list[dict[str, Any]]
    assessments: list[dict[str, Any]]
    action_items: list[dict[str, Any]] = Field(default_factory=list)
    trace: list[dict[str, Any]] = Field(default_factory=list)
    failed_sources: list[str] = Field(default_factory=list)
    source_tasks: list[dict[str, Any]] = Field(default_factory=list)


class ReportWriterTool(BaseTool):
    """Write approved local SignalHarness output artifacts."""

    name = "report_writer"
    description = "Write SignalHarness JSON outputs or the Markdown radar digest."
    input_model = ReportWriterInput

    def is_read_only(self, arguments: ReportWriterInput) -> bool:
        return False

    async def execute(
        self,
        arguments: ReportWriterInput,
        context: ToolExecutionContext,
    ) -> ToolResult:
        output_dir = Path(
            context.metadata.get("output_dir") or context.cwd / "outputs"
        ).resolve()
        signals = [SignalEvent.model_validate(item) for item in arguments.signals]
        assessments = [
            SignalAssessment.model_validate(item) for item in arguments.assessments
        ]
        if arguments.action == "write_radar_digest":
            path = write_radar_digest(output_dir, signals, assessments)
            return ToolResult(output=str(path), metadata={"path": str(path)})
        if arguments.action == "write_run_summary":
            path = write_run_summary(
                output_dir,
                signals,
                assessments,
                arguments.failed_sources,
                [SourceTask.model_validate(item) for item in arguments.source_tasks],
            )
            return ToolResult(output=str(path), metadata={"path": str(path)})
        paths = write_json_outputs(
            output_dir,
            signals,
            assessments,
            arguments.action_items,
            [TraceStep.model_validate(item) for item in arguments.trace],
        )
        return ToolResult(
            output=json.dumps({key: str(path) for key, path in paths.items()}),
            metadata={"paths": {key: str(path) for key, path in paths.items()}},
        )
