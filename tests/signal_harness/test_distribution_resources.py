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
from signal_harness.ui.demo import demo_asset_dir


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


def test_bundled_capability_eval_resources_are_present(tmp_path: Path) -> None:
    capability = resolve_example_path(
        tmp_path, "examples/signal_harness/capability_golden_v1.json"
    )
    narrative_seed = resolve_example_path(
        tmp_path, "examples/signal_harness/narrative_calibration_seed.json"
    )
    capability_payload = json.loads(capability.read_text(encoding="utf-8"))
    seed_payload = json.loads(narrative_seed.read_text(encoding="utf-8"))

    assert capability_payload["suite"] == "signalharness-capability-v1"
    assert capability_payload["dataset_role"] == "capability"
    assert len(capability_payload["cases"]) == 32
    assert seed_payload["version"] == "narrative-calibration-v1"
    assert len(seed_payload["case_ids"]) == 16


def test_compiled_react_demo_assets_are_packaged_with_ui() -> None:
    assets = demo_asset_dir()

    html = (assets / "demo.html").read_text(encoding="utf-8")
    javascript = (assets / "demo.js").read_text(encoding="utf-8")
    stylesheet = (assets / "demo.css").read_text(encoding="utf-8")
    assert '<div id="root"></div>' in html
    assert '/demo-assets/demo.js' in html
    assert '/demo-assets/demo.css' in html
    assert "EventSource" in javascript
    assert "/intelligence/projects/" in javascript
    assert "product.progress" in javascript
    assert "mock-agent" not in javascript
    assert "impact_score" not in javascript
    assert "--color-canvas" in stylesheet


def test_narrative_review_static_assets_are_packaged_with_ui() -> None:
    assets = demo_asset_dir()

    assert (assets / "narrative_review.html").is_file()
    assert (assets / "narrative_review.css").is_file()
    assert (assets / "narrative_review.js").is_file()
