from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from signal_harness.cli import app


runner = CliRunner()


def _base(project_root: Path, tmp_path: Path) -> list[str]:
    return [
        "--cwd",
        str(project_root),
        "--state-dir",
        str(tmp_path / "state"),
    ]


def test_agent_json_cli_scan_report_changes_detail_and_export(
    project_root: Path,
    tmp_path: Path,
) -> None:
    scan = runner.invoke(
        app,
        [
            "scan",
            "--fixture",
            "examples/signal_harness/sample_events.json",
            "--mode",
            "demo",
            "--max-events",
            "2",
            "--output-dir",
            str(tmp_path / "outputs"),
            "--json",
            *_base(project_root, tmp_path),
        ],
    )
    assert scan.exit_code == 0, scan.output
    scan_payload = json.loads(scan.stdout)
    scan_id = scan_payload["scan_id"]
    assert scan_payload["report"]["scan_id"] == scan_id
    assert scan_payload["report"]["stats"]["all_change_count"] == 4
    assert scan_payload["report"]["stats"]["analyzed_count"] == 2
    assert "Generated SignalHarness" not in scan.stdout
    assert "SignalHarness Radar" not in scan.stdout

    report = runner.invoke(
        app,
        ["report", "--scan", scan_id, "--json", *_base(project_root, tmp_path)],
    )
    assert report.exit_code == 0, report.output
    assert json.loads(report.stdout)["scan_id"] == scan_id

    changes = runner.invoke(
        app,
        [
            "changes",
            "--scan",
            scan_id,
            "--limit",
            "2",
            "--json",
            *_base(project_root, tmp_path),
        ],
    )
    assert changes.exit_code == 0, changes.output
    page = json.loads(changes.stdout)
    assert page["scan_id"] == scan_id
    assert page["all_count"] == 4
    assert page["returned"] == 2
    change_id = page["items"][0]["change_id"]

    detail = runner.invoke(
        app,
        ["change", change_id, "--scan", scan_id, "--json", *_base(project_root, tmp_path)],
    )
    assert detail.exit_code == 0, detail.output
    detail_payload = json.loads(detail.stdout)
    assert detail_payload["change_id"] == change_id

    export_path = tmp_path / "export.md"
    exported = runner.invoke(
        app,
        [
            "export",
            "--scan",
            scan_id,
            "--mode",
            "change",
            "--change-id",
            change_id,
            "--out",
            str(export_path),
            "--json",
            *_base(project_root, tmp_path),
        ],
    )
    assert exported.exit_code == 0, exported.output
    export_payload = json.loads(exported.stdout)
    assert export_payload["scan_id"] == scan_id
    assert export_payload["path"] == str(export_path)
    markdown = export_path.read_text(encoding="utf-8")
    assert "Change ID" in markdown
    assert "llm_agent_call" not in markdown


def test_product_json_cli_errors_are_machine_readable(
    project_root: Path,
    tmp_path: Path,
) -> None:
    result = runner.invoke(
        app,
        ["changes", "--analysis", "broken", "--json", *_base(project_root, tmp_path)],
    )
    assert result.exit_code == 2
    assert result.stdout == ""
    payload = json.loads(result.stderr)
    assert payload["error"]["code"] == "product_intelligence_error"
