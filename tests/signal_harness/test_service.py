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
        assert run["all_changes"] == 4
        assert run["changes_url"] == f"/runs/{run_id}/changes"
        fetched = client.get(f"/runs/{run_id}")
        assert fetched.status_code == 200
        assert fetched.json()["run_id"] == run_id

        signals = client.get("/signals", params={"run_id": run_id})
        assert signals.status_code == 200
        assert signals.json()["count"] == 4

        changes = client.get(f"/runs/{run_id}/changes", params={"limit": 2})
        assert changes.status_code == 200
        assert changes.json()["count"] == 4
        assert changes.json()["returned"] == 2
        assert changes.json()["has_more"] is True
        next_changes = client.get(
            f"/runs/{run_id}/changes", params={"offset": 2, "limit": 2}
        )
        assert next_changes.status_code == 200
        assert next_changes.json()["returned"] == 2
        assert next_changes.json()["has_more"] is False

        coverage = client.get(f"/runs/{run_id}/coverage")
        assert coverage.status_code == 200
        coverage_payload = coverage.json()
        assert coverage_payload["scan_id"] == run_id
        assert coverage_payload["coverage_status"] == "complete"
        assert coverage_payload["sources"][0]["coverage_status"] == "complete"

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
        assert feedback.json()["golden_candidate_id"] is None

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
        assert saved.json()["golden_candidate_id"]

        candidate_store = (
            state_dir / "projects" / "signalharness" / "golden_candidates.json"
        )
        candidates = __import__("json").loads(candidate_store.read_text(encoding="utf-8"))
        assert len(candidates) == 1
        candidate = candidates[0]
        assert candidate["event_id"] == "demo-001"
        assert candidate["feedback_label"] == "false_positive"
        assert candidate["event"]["event_id"] == "demo-001"
        assert candidate["assessment"]["event_id"] == "demo-001"
        assert candidate["change_id"]
        assert candidate["event_revision_id"] is not None

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


def test_narrative_blind_review_api_hides_variant_and_persists_complete_labels(
    project_root: Path,
    tmp_path: Path,
) -> None:
    import json

    from signal_harness.narrative_calibration import (
        NarrativeOutputForReview,
        NarrativeReviewFile,
        NarrativeReviewPair,
    )

    output_dir = tmp_path / "outputs"
    review_dir = output_dir / "narrative-calibration"
    review_dir.mkdir(parents=True)
    review = NarrativeReviewFile(
        version="narrative-calibration-v1",
        capability_suite="signalharness-capability-v1",
        source_mode="agent",
        source_provider="qwen",
        source_model="qwen-plus",
        calibration_eligible=True,
        rubric_dimensions=["factual_grounding", "project_specificity"],
        minimum_labeled_pairs=1,
        pairs=[
            NarrativeReviewPair(
                pair_id="narrative-pair-test",
                case_id="cap-001",
                case_title="Pydantic v3 changes JSON Schema validation defaults",
                truth_status="verified",
                A=NarrativeOutputForReview(
                    decision="alert",
                    impact_score=88,
                    what_changed_zh="A 发生了什么",
                    why_relevant_zh="A 为什么相关",
                    recommended_actions_zh=["A 建议"],
                ),
                B=NarrativeOutputForReview(
                    decision="save",
                    impact_score=52,
                    what_changed_zh="B 发生了什么",
                    why_relevant_zh="B 为什么相关",
                    recommended_actions_zh=["B 建议"],
                ),
                dimension_preferences={
                    "factual_grounding": None,
                    "project_specificity": None,
                },
            )
        ],
    )
    review_path = review_dir / "narrative_pairs.review.json"
    review_path.write_text(review.model_dump_json(indent=2) + "\n", encoding="utf-8")
    app = create_app(
        cwd=project_root,
        output_dir=output_dir,
        state_dir=tmp_path / "state",
    )

    with TestClient(app) as client:
        page = client.get("/eval/narrative")
        assert page.status_code == 200
        assert "Narrative 盲测" in page.text

        data = client.get("/eval/narrative/data")
        assert data.status_code == 200, data.text
        payload = data.json()
        assert payload["status"]["labeled_pairs"] == 0
        assert payload["pairs"][0]["case_id"] == "cap-001"
        assert "decision" not in payload["pairs"][0]["A"]
        assert "impact_score" not in payload["pairs"][0]["A"]
        assert "variant" not in json.dumps(payload).lower()

        incomplete = client.post(
            "/eval/narrative/pairs/narrative-pair-test",
            json={
                "human_preference": "A",
                "dimension_preferences": {"factual_grounding": "A"},
                "human_note": "missing one dimension",
            },
        )
        assert incomplete.status_code == 400

        saved = client.post(
            "/eval/narrative/pairs/narrative-pair-test",
            json={
                "human_preference": "B",
                "dimension_preferences": {
                    "factual_grounding": "B",
                    "project_specificity": "tie",
                },
                "human_note": "B 更贴合项目。",
            },
        )
        assert saved.status_code == 200, saved.text
        saved_payload = saved.json()
        assert saved_payload["status"]["labeled_pairs"] == 1
        assert saved_payload["status"]["ready_for_judge_calibration"] is True

    persisted = json.loads(review_path.read_text(encoding="utf-8"))
    assert persisted["pairs"][0]["human_preference"] == "B"
    assert persisted["pairs"][0]["dimension_preferences"]["project_specificity"] == "tie"
    assert persisted["pairs"][0]["human_note"] == "B 更贴合项目。"
