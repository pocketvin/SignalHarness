from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest

from signal_harness.agent_integration.mode import RunMode
from signal_harness.runtime.tools_base import ToolExecutionContext, ToolResult
from signal_harness.runtime.workflow import SignalHarnessWorkflow
from signal_harness.tools.security_osv import SecurityOsvTool


@pytest.mark.asyncio
async def test_workflow_collects_local_git_and_osv_into_frozen_scan(
    project_root: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_osv(
        self: SecurityOsvTool,
        arguments: Any,
        context: ToolExecutionContext,
    ) -> ToolResult:
        del self, context
        assert any(
            item.name == "pydantic" and item.version == "2.13.4"
            for item in arguments.dependencies
        )
        return ToolResult(
            output=json.dumps(
                [
                    {
                        "id": "PYSEC-2026-999",
                        "aliases": ["CVE-2026-9999"],
                        "summary": "Synthetic project vulnerability",
                        "details": "Synthetic integration fixture",
                        "published": "2026-09-08T00:00:00Z",
                        "modified": "2026-09-08T12:00:00Z",
                        "matched_package": "pydantic",
                        "matched_ecosystem": "PyPI",
                        "matched_version": "2.13.4",
                        "matched_dependencies": [
                            {
                                "name": "pydantic",
                                "ecosystem": "PyPI",
                                "version": "2.13.4",
                            }
                        ],
                    }
                ]
            ),
            metadata={
                "coverage_status": "complete",
                "history_limited": False,
                "pages_fetched": 1,
                "diagnostics": [],
            },
        )

    monkeypatch.setattr(SecurityOsvTool, "execute", fake_osv)
    watchlist = tmp_path / "watchlist.yaml"
    watchlist.write_text(
        f"""local_git:
  repositories:
    - name: SignalHarness
      path: {project_root}
security:
  osv:
    enabled: true
""",
        encoding="utf-8",
    )
    workflow = SignalHarnessWorkflow(
        cwd=project_root,
        config_dir=project_root / "configs",
        project_profile_path=project_root / "configs/project_profile.yaml",
        watchlist_path=watchlist,
        output_dir=tmp_path / "outputs",
        state_dir=tmp_path / "state",
        mode=RunMode.DEMO,
        project_id="signalharness",
    )
    result = await workflow.scan(
        since=datetime(2026, 9, 1, tzinfo=timezone.utc),
        until=datetime(2026, 9, 10, tzinfo=timezone.utc),
        window_mode="custom",
        max_events=20,
    )

    source_types = {task.source_type for task in result.source_tasks}
    assert {"local_git_commit", "security_advisory"} <= source_types
    rows = workflow.ledger.list_scan_changes(result.scan_id, offset=0, limit=500)
    persisted_types = {str(item["source_type"]) for item in rows.items}
    assert {"local_git_commit", "security_advisory"} <= persisted_types
    assert result.coverage_status == "complete"
