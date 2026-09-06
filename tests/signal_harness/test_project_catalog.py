from __future__ import annotations

import shutil
from pathlib import Path

from fastapi.testclient import TestClient

from signal_harness.projects.catalog import project_catalog, project_option
from signal_harness.service import create_app


def _write_project_config(root: Path, project_id: str, *, ignore_checkpoint: bool) -> None:
    profile = root / f"{project_id}-profile.yaml"
    watchlist = root / f"{project_id}-watchlist.yaml"
    ignored = "\n  - checkpoint" if ignore_checkpoint else "\n  - consumer giveaway"
    profile.write_text(
        "project_name: " + project_id + "\n"
        "goal: test project scoping\n"
        "tech_stack:\n  - Python\n"
        "critical_modules: []\n"
        "dependencies: []\n"
        "monitored_ecosystem: []\n"
        "competitors: []\n"
        "focus_keywords: []\n"
        "ignore_keywords:" + ignored + "\n",
        encoding="utf-8",
    )
    watchlist.write_text("github:\n  repositories: []\nrss:\n  feeds: []\n", encoding="utf-8")
    entry = root / "projects" / f"{project_id}.yaml"
    entry.write_text(
        f"id: {project_id}\n"
        f"name: {project_id}\n"
        f"description: {project_id} test profile\n"
        f"project_profile: ../{profile.name}\n"
        f"watchlist: ../{watchlist.name}\n"
        f"default: {'true' if project_id == 'primary' else 'false'}\n",
        encoding="utf-8",
    )


def _config_dir(project_root: Path, tmp_path: Path) -> Path:
    root = tmp_path / "configs"
    (root / "projects").mkdir(parents=True)
    shutil.copy(project_root / "configs/signal_policy.yaml", root / "signal_policy.yaml")
    _write_project_config(root, "primary", ignore_checkpoint=False)
    _write_project_config(root, "ignore-checkpoint", ignore_checkpoint=True)
    return root


def test_project_catalog_loads_multiple_projects(project_root: Path, tmp_path: Path) -> None:
    config_dir = _config_dir(project_root, tmp_path)
    options = project_catalog(config_dir)

    assert [item.id for item in options] == ["ignore-checkpoint", "primary"]
    assert project_option("primary", config_dir).is_default is True


def _completed_payload(response) -> dict:
    event_name = ""
    for line in response.iter_lines():
        if line.startswith("event:"):
            event_name = line.split(":", 1)[1].strip()
        elif line.startswith("data:") and event_name == "run.completed":
            import json

            return json.loads(line.split(":", 1)[1].strip())
    raise AssertionError("run.completed SSE event was not observed")


def test_selected_project_changes_runtime_profile(project_root: Path, tmp_path: Path) -> None:
    config_dir = _config_dir(project_root, tmp_path)
    app = create_app(
        cwd=project_root,
        config_dir=config_dir,
        output_dir=tmp_path / "outputs",
        state_dir=tmp_path / "state",
    )
    with TestClient(app) as client:
        meta = client.get("/demo/meta").json()
        assert {item["id"] for item in meta["projects"]} == {
            "primary",
            "ignore-checkpoint",
        }
        response = client.post(
            "/stream-runs",
            json={
                "project_id": "ignore-checkpoint",
                "data_source": "fixture",
                "mode": "mock-agent",
                "max_events": 4,
            },
        )
        assert response.status_code == 202
        run = response.json()
        assert run["project_id"] == "ignore-checkpoint"
        with client.stream("GET", run["events_url"]) as stream:
            completed = _completed_payload(stream)
        assert completed["run"]["project_id"] == "ignore-checkpoint"
        assessments = {item["event_id"]: item for item in completed["assessments"]}
        assert assessments["demo-001"]["category"] == "noise"

        bad = client.post(
            "/stream-runs",
            json={
                "project_id": "missing-project",
                "data_source": "fixture",
                "mode": "mock-agent",
            },
        )
        assert bad.status_code == 400
        assert bad.json()["detail"] == "Unknown project selection"
