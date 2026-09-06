from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from signal_harness.service import create_app


def test_service_runs_mock_agent_and_exposes_trace_feedback(
    project_root: Path,
    tmp_path: Path,
) -> None:
    output_dir = tmp_path / "outputs"
    state_dir = tmp_path / "state"
    app = create_app(
        cwd=project_root,
        output_dir=output_dir,
        state_dir=state_dir,
    )

    with TestClient(app) as client:
        health = client.get("/health")
        assert health.status_code == 200
        assert health.json() == {"status": "ok", "service": "signalharness"}

        created = client.post(
            "/runs",
            json={"mode": "mock-agent"},
        )
        assert created.status_code == 201, created.text
        run = created.json()
        run_id = run["run_id"]
        assert run["status"] == "success"
        assert run["signals"] == 4
        fetched = client.get(f"/runs/{run_id}")
        assert fetched.status_code == 200
        assert fetched.json()["run_id"] == run_id

        signals = client.get("/signals", params={"run_id": run_id})
        assert signals.status_code == 200
        assert signals.json()["count"] == 4

        trace = client.get(f"/runs/{run_id}/trace")
        assert trace.status_code == 200
        assert trace.json()["count"] > 0
        assert any(
            item.get("step") == "llm_agent_call"
            for item in trace.json()["items"]
            if isinstance(item, dict)
        )

        feedback = client.post(
            "/feedback",
            json={
                "run_id": run_id,
                "signal_id": "demo-001",
                "label": "useful",
                "note": "Strong checkpoint signal",
            },
        )
        assert feedback.status_code == 200, feedback.text
        assert feedback.json()["policy_applied"] is False
        assert feedback.json()["proposal_id"]

    assert (output_dir / "service-runs" / run_id / "service_run.json").exists()
    assert (state_dir / "projects" / "signalharness" / "feedback_memory.json").exists()


def test_service_rejects_fixture_outside_project_root(
    project_root: Path,
    tmp_path: Path,
) -> None:
    outside = tmp_path / "outside.json"
    outside.write_text("[]", encoding="utf-8")
    app = create_app(
        cwd=project_root,
        output_dir=tmp_path / "outputs",
        state_dir=tmp_path / "state",
    )

    with TestClient(app) as client:
        response = client.post(
            "/runs",
            json={"mode": "demo", "fixture": str(outside)},
        )

    assert response.status_code == 400
    assert response.json()["detail"] == "Fixture must stay inside the project root"


def test_service_rejects_invalid_run_id(project_root: Path, tmp_path: Path) -> None:
    app = create_app(
        cwd=project_root,
        output_dir=tmp_path / "outputs",
        state_dir=tmp_path / "state",
    )
    with TestClient(app) as client:
        response = client.get("/runs/..%2Fsecrets")
    assert response.status_code == 404


def test_service_feedback_persists_by_project_and_is_isolated(
    project_root: Path,
    tmp_path: Path,
) -> None:
    output_dir = tmp_path / "outputs"
    state_dir = tmp_path / "state"
    app = create_app(cwd=project_root, output_dir=output_dir, state_dir=state_dir)
    with TestClient(app) as client:
        first = client.post("/runs", json={"mode": "mock-agent", "project_id": "signalharness"})
        assert first.status_code == 201
        run_id = first.json()["run_id"]
        saved = client.post(
            "/feedback",
            json={
                "run_id": run_id,
                "signal_id": "demo-001",
                "label": "false_positive",
                "note": "project-specific correction",
            },
        )
        assert saved.status_code == 200
        assert saved.json()["project_id"] == "signalharness"

        second = client.post("/runs", json={"mode": "mock-agent", "project_id": "signalharness"})
        assert second.status_code == 201
        second_trace = client.get(f"/runs/{second.json()['run_id']}/trace").json()["items"]
        second_noise = next(item for item in second_trace if item.get("step") == "noise_filter")
        assert "duplicate_hash" in second_noise.get("detail", "")

        other = client.post(
            "/runs",
            json={"mode": "mock-agent", "project_id": "example-agent-service"},
        )
        assert other.status_code == 201
        other_trace = client.get(f"/runs/{other.json()['run_id']}/trace").json()["items"]
        other_noise = next(item for item in other_trace if item.get("step") == "noise_filter")
        assert "duplicate_hash" not in other_noise.get("detail", "")

    signal_feedback = state_dir / "projects" / "signalharness" / "feedback_memory.json"
    example_feedback = state_dir / "projects" / "example-agent-service" / "feedback_memory.json"
    assert "project-specific correction" in signal_feedback.read_text(encoding="utf-8")
    assert not example_feedback.exists()
    assert (state_dir / "projects" / "signalharness" / "signal_memory.json").exists()
    assert (state_dir / "projects" / "example-agent-service" / "signal_memory.json").exists()
