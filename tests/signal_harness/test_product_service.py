from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient
from typer.testing import CliRunner

from signal_harness.cli import app as cli_app
from signal_harness.service import create_app


runner = CliRunner()


def test_rest_product_projection_and_cli_agree_on_same_scan(
    project_root: Path,
    tmp_path: Path,
) -> None:
    output_dir = tmp_path / "outputs"
    state_dir = tmp_path / "state"
    app = create_app(cwd=project_root, output_dir=output_dir, state_dir=state_dir)

    with TestClient(app) as client:
        created = client.post("/runs", json={"mode": "demo", "max_events": 2})
        assert created.status_code == 201, created.text
        run = created.json()
        run_id = run["run_id"]
        assert run["product_url"] == f"/runs/{run_id}/product"
        assert run["report_url"] == f"/runs/{run_id}/report"

        product_one = client.get(
            f"/runs/{run_id}/product", params={"top": 1, "all_limit": 2}
        )
        product_three = client.get(
            f"/runs/{run_id}/product", params={"top": 3, "all_limit": 2}
        )
        assert product_one.status_code == product_three.status_code == 200
        one = product_one.json()
        three = product_three.json()
        assert one["scan_id"] == three["scan_id"] == run_id
        assert one["report"]["stats"]["all_change_count"] == 4
        assert three["report"]["stats"]["all_change_count"] == 4
        assert one["all_changes"]["all_count"] == three["all_changes"]["all_count"] == 4
        assert len(one["top_changes"]) == 1
        assert len(three["top_changes"]) >= 1

        report = client.get(f"/runs/{run_id}/report", params={"top": 2})
        assert report.status_code == 200
        rest_report = report.json()
        assert rest_report["scan_id"] == run_id
        assert rest_report["stats"]["analyzed_count"] == 2

        unanalyzed = client.get(
            f"/runs/{run_id}/changes",
            params={"analysis": "unanalyzed", "limit": 10},
        )
        assert unanalyzed.status_code == 200
        assert unanalyzed.json()["count"] == 2
        first_change_id = one["all_changes"]["items"][0]["change_id"]
        detail = client.get(f"/runs/{run_id}/changes/{first_change_id}")
        assert detail.status_code == 200
        assert detail.json()["change_id"] == first_change_id

    cli = runner.invoke(
        cli_app,
        [
            "report",
            "--scan",
            run_id,
            "--json",
            "--top",
            "2",
            "--cwd",
            str(project_root),
            "--state-dir",
            str(state_dir),
        ],
    )
    assert cli.exit_code == 0, cli.output
    cli_report = json.loads(cli.stdout)
    assert cli_report["scan_id"] == rest_report["scan_id"]
    assert cli_report["stats"] == rest_report["stats"]
    assert cli_report["top_change_ids"] == rest_report["top_change_ids"]
