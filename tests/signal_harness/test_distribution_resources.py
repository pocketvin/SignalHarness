from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient

from signal_harness.resources import (
    bundled_config_dir,
    bundled_examples_dir,
    resolve_config_dir,
    resolve_example_path,
)
from signal_harness.service import create_app


def test_default_resources_fall_back_outside_repository(tmp_path: Path) -> None:
    config = resolve_config_dir(tmp_path, "configs")
    fixture = resolve_example_path(tmp_path, "examples/signal_harness/sample_events.json")

    assert config == bundled_config_dir().resolve()
    assert fixture == (bundled_examples_dir() / "sample_events.json").resolve()
    assert (config / "signal_policy.yaml").is_file()
    assert fixture.is_file()


def test_service_works_from_cwd_without_repo_resources(tmp_path: Path) -> None:
    app = create_app(
        cwd=tmp_path,
        output_dir=tmp_path / "outputs",
        state_dir=tmp_path / "state",
    )

    with TestClient(app) as client:
        meta = client.get("/demo/meta")
        assert meta.status_code == 200
        payload = meta.json()
        assert payload["default_project_id"] == "signalharness"
        assert {item["id"] for item in payload["projects"]} >= {
            "signalharness",
            "example-agent-service",
        }

        created = client.post(
            "/stream-runs",
            json={
                "mode": "mock-agent",
                "data_source": "fixture",
                "fixture": "examples/signal_harness/sample_events.json",
            },
        )
        assert created.status_code == 202, created.text
        run = created.json()
        with client.stream("GET", run["events_url"]) as response:
            assert response.status_code == 200
            body = "\n".join(response.iter_lines())
        assert "run.completed" in body
        final = client.get(f"/stream-runs/{run['run_id']}").json()
        assert final["status"] == "success"


def test_bundled_regression_files_are_present(tmp_path: Path) -> None:
    expectations = resolve_example_path(
        tmp_path, "examples/signal_harness/regression_expectations.json"
    )
    baseline = resolve_example_path(tmp_path, "examples/signal_harness/regression_baseline.json")
    assert json.loads(expectations.read_text(encoding="utf-8"))["suite"] == "resume-v1"
    assert json.loads(baseline.read_text(encoding="utf-8"))["passed"] is True
