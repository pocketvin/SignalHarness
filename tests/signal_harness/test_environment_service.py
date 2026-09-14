from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from mcp import Client
from typer.testing import CliRunner

from signal_harness.cli import app as cli_app
from signal_harness.intelligence.usage import inspect_usage
from signal_harness.learning.proposal_risk import ProposalRiskReport
from signal_harness.learning.staging import StagedLearningProposal, save_learning_staging
from signal_harness.mcp_server import build_mcp_server
from signal_harness.projects.state import prepare_project_state
from signal_harness.providers.task_policy import TaskPolicy
from signal_harness.runtime.workflow import CollectionBatch, SignalHarnessWorkflow
from signal_harness.service import create_app
from signal_harness.utils.fs import atomic_write_text
from test_environment_pipeline import ScriptedIntelligenceProvider


@pytest.fixture
def intelligence_app(project_root, tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(TaskPolicy, "providers", lambda self, role: ["offline"])
    monkeypatch.setattr(
        TaskPolicy, "create_provider", lambda self, name, role: ScriptedIntelligenceProvider(calls)
    )

    async def collect(self, *args, **kwargs):
        return CollectionBatch(
            events=[
                {
                    "event_id": f"test-source-{i}",
                    "source_type": "web_change",
                    "source_name": f"source-{i % 4}",
                    "title": f"工具注册接口更新 {i:03}",
                    "content": "工具注册的协议版本现在明确标注。",
                    "url": f"https://example.com/{i}",
                    "published_at": (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat(),
                    "raw_payload": {"source_authority": "official"},
                }
                for i in range(42)
            ],
            failed_sources=[],
            source_tasks=[],
        )

    monkeypatch.setattr(SignalHarnessWorkflow, "_collect_watchlist", collect)

    async def fetch(url, **kwargs):
        return url, "text/plain", "工具注册的协议版本现在明确标注。"

    monkeypatch.setattr("signal_harness.intelligence.deep_dive.fetch_public_text", fetch)
    monkeypatch.setattr(
        "signal_harness.intelligence.deep_dive.inspect_usage",
        lambda *args: {
            "references": [],
            "fingerprint": "no-authorized-roots",
            "files_checked": 0,
            "limited": False,
            "notice": "没有本地源码证据。",
        },
    )
    app = create_app(
        cwd=project_root,
        config_dir=project_root / "configs",
        output_dir=tmp_path / "outputs",
        state_dir=tmp_path / "state",
    )
    with TestClient(app) as client:
        project = client.get("/intelligence/meta").json()["default_project_id"]
        yield app, client, project, calls


def run_scan(client, project):
    response = client.post(f"/intelligence/projects/{project}/scans", json={"window": "since_last"})
    assert response.status_code == 202, response.text
    run = response.json()
    with client.stream("GET", run["events_url"]) as stream:
        text = "\n".join(stream.iter_lines())
    assert "event: product.progress" in text
    assert "event: trace.step" in text
    assert "event: trace.step.updated" in text
    assert '"index":' in text and '"trace":' in text
    assert "event: run.completed" in text
    home = client.get(f"/intelligence/projects/{project}").json()
    assert home["report"] is not None, text[-4000:]
    return run, home["report"]


def test_product_post_runs_full_pipeline_without_legacy_deep_agents(intelligence_app):
    app, client, project, calls = intelligence_app
    run, report = run_scan(client, project)
    assert report["counts"]["changes"] == report["counts"]["interpreted"] == 42
    assert report["counts"]["automatic_deep_dives"] == 0
    assert sum(call.agent_name == "ChangeInterpreter" for call in calls) == 4
    assert sum(call.agent_name == "EnvironmentSynthesizer" for call in calls) == 1
    assert len(calls[-1].input_payload["corpus"]) == 42
    assert {call.agent_name for call in calls} == {"ChangeInterpreter", "EnvironmentSynthesizer"}
    assert run["window"]["first_use"] is True
    session = app.state.stream_manager.get(run["run_id"])
    assert session.frozen_window["to"] == report["window"]["to"]
    with app.state.deep_dive_manager.repository(project).connect() as db:
        assert db.execute("SELECT count(*) FROM deep_dive_jobs").fetchone()[0] == 0


@pytest.mark.parametrize("field", ["mode", "provider_id", "model", "max_events", "fixture"])
def test_normal_ui_cannot_select_model_or_demo(intelligence_app, field):
    _, client, project, _ = intelligence_app
    response = client.post(
        f"/intelligence/projects/{project}/scans", json={"window": "since_last", field: "forbidden"}
    )
    assert response.status_code == 422


def test_deep_dive_explicit_post_idempotent_and_no_report_mutation(intelligence_app):
    app, client, project, calls = intelligence_app
    _, report = run_scan(client, project)
    base = f"/intelligence/projects/{project}/reports/{report['scan_id']}"
    change = client.get(base + "/changes?view=all&limit=1").json()["items"][0]
    path = f"{base}/changes/{change['change_id']}"
    count = len(calls)
    assert client.get(path).status_code == 200
    assert len(calls) == count  # GET must not create a model call.
    first = client.post(path + "/deep-dive", json={}).json()
    second = client.post(path + "/deep-dive", json={}).json()
    assert first["job_id"] == second["job_id"]
    with client.stream("GET", first["events_url"]) as stream:
        text = "\n".join(stream.iter_lines())
    assert "deep.updated" in text
    saved = client.get(f"/intelligence/projects/{project}/deep-dives/{first['job_id']}").json()
    assert saved["status"] == "complete", saved
    assert len(calls) == count + 1
    assert saved["usage"]["files_checked"] == 0
    assert "audit" not in saved and "cache_key" not in saved
    third = client.post(path + "/deep-dive", json={}).json()
    assert third["status"] == "complete"
    assert len(calls) == count + 1
    assert client.get(base).json() == report
    assert (
        client.get(
            f"/intelligence/projects/missing-project/deep-dives/{first['job_id']}"
        ).status_code
        == 404
    )


def test_direction_evidence_pagination_and_history(intelligence_app):
    _, client, project, _ = intelligence_app
    _, report = run_scan(client, project)
    base = f"/intelligence/projects/{project}/reports/{report['scan_id']}"
    direction = report["directions"][0]
    page = client.get(base + "/changes", params={"direction_id": direction["direction_id"]}).json()
    assert page["count"] == 3
    assert {item["change_id"] for item in page["items"]} == set(direction["supporting_change_ids"])
    assert client.get(base + "/changes?query=041").json()["count"] == 1
    assert client.get(base + "/changes?query=%25").json()["count"] == 0
    assert client.get(base + "/changes?offset=-1").status_code == 422
    history = client.get(
        f"/intelligence/projects/{project}/directions/{direction['direction_id']}/history"
    ).json()
    assert history["items"][0]["revision_id"] == direction["revision_id"]


def test_saved_environment_trace_is_project_scoped_and_product_safe(intelligence_app):
    app, client, project, _ = intelligence_app
    run, report = run_scan(client, project)
    trace_path = (
        app.state.environment_application.output_dir
        / "service-runs"
        / run["run_id"]
        / "agent_trace.json"
    )
    raw_trace = json.loads(trace_path.read_text(encoding="utf-8"))
    model_step = next(step for step in raw_trace if step.get("step") == "llm_agent_call")
    model_step["metadata"] = {
        "attempt": 1,
        "validation_code": "unverified_trend_velocity",
        "repair_mode": "localized",
        "input_bytes": 999999,
        "public_summary": "internal-only",
    }
    trace_path.write_text(json.dumps(raw_trace, ensure_ascii=False), encoding="utf-8")

    response = client.get(
        f"/intelligence/projects/{project}/reports/{report['scan_id']}/trace"
    )
    assert response.status_code == 200
    trace = response.json()
    assert trace
    exposed = next(step for step in trace if step["step"] == "llm_agent_call")
    assert exposed["metadata"] == {
        "attempt": 1,
        "validation_code": "unverified_trend_velocity",
        "repair_mode": "localized",
    }
    assert all("detail" not in step for step in trace)
    assert all("source_tasks" not in step for step in trace)
    assert all("error" not in step for step in trace)
    assert (
        client.get(
            f"/intelligence/projects/missing-project/reports/{report['scan_id']}/trace"
        ).status_code
        == 404
    )


def test_task_policy_honors_new_model_pin_without_changing_legacy_env(project_root, monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-only-secret")
    monkeypatch.setenv("DEEPSEEK_BASE_URL", "https://example.com/v1")
    monkeypatch.setenv("DEEPSEEK_MODEL", "deepseek-v4-flash")
    provider = TaskPolicy.load(project_root / "configs").create_provider("deepseek", "synthesis")
    assert provider.model == "deepseek-v4-pro"
    assert provider.profile.max_input_tokens > 100000
    assert provider.request_options == {"thinking": {"type": "enabled"}, "reasoning_effort": "high"}
    asyncio.run(provider.close())


def test_task_policy_uses_supported_kimi_reasoning_tiers(project_root, monkeypatch):
    monkeypatch.setenv("KIMI_API_KEY", "test-only-secret")
    monkeypatch.setenv("KIMI_BASE_URL", "https://example.com/v1")
    policy = TaskPolicy.load(project_root / "configs")
    shallow = policy.create_provider("kimi", "shallow")
    synthesis = policy.create_provider("kimi", "synthesis")
    deep_dive = policy.create_provider("kimi", "deep_dive")
    try:
        assert shallow.profile.reasoning_effort == "low"
        assert synthesis.profile.reasoning_effort == "low"
        assert deep_dive.profile.reasoning_effort == "high"
        assert synthesis.profile.max_output_tokens == 8192
        assert shallow.profile.recommended_temperature == 1.0
        assert synthesis.profile.recommended_temperature == 1.0
    finally:
        asyncio.run(shallow.close())
        asyncio.run(synthesis.close())
        asyncio.run(deep_dive.close())


def test_usage_skips_private_symlinks_secrets_and_changes_cache_fingerprint(tmp_path: Path):
    root = tmp_path / "project"
    root.mkdir()
    (root / "client.py").write_text("from openai import OpenAI\nclient = OpenAI()\n")
    (root / ".env").write_text("OPENAI_API_KEY=should-never-be-read")
    (root / "Private-NoAI").mkdir()
    (root / "Private-NoAI" / "private.py").write_text("openai private secret")
    (root / "unsafe.py").write_text('openai_api_key = "test-hidden-value"')
    outside = tmp_path / "outside.py"
    outside.write_text("from openai import STOLEN")
    (root / "linked.py").symlink_to(outside)
    first = inspect_usage([root], "OpenAI")
    assert len(first["references"]) == 2
    assert (
        "STOLEN" not in str(first)
        and "private.py" not in str(first)
        and "test-hidden" not in str(first)
    )
    (root / "client.py").write_text("from openai import AsyncOpenAI")
    second = inspect_usage([root], "OpenAI")
    assert first["fingerprint"] != second["fingerprint"]


def test_product_schedule_uses_environment_pipeline_and_hides_model(intelligence_app):
    app, client, project, _ = intelligence_app
    base = f"/intelligence/projects/{project}/schedules"
    response = client.post(base, json={"cadence": "24h", "timezone": "Asia/Shanghai"})
    assert response.status_code == 201, response.text
    item = client.get(base).json()["items"][0]
    assert "mode" not in item and "provider_id" not in item and "max_events" not in item
    original = app.state.schedule_manager.ledger(project).list_schedules(project_id=project)[0]
    assert original["intelligence_pipeline"] is True
    assert original["max_events"] is None
    assert client.delete(base + "/" + item["schedule_id"]).status_code == 200
    assert not client.get(base).json()["items"][0]["enabled"]


def test_product_schedule_rejects_ui_model_fields(intelligence_app):
    _, client, project, _ = intelligence_app
    assert (
        client.post(
            f"/intelligence/projects/{project}/schedules",
            json={"cadence": "24h", "provider_id": "kimi"},
        ).status_code
        == 422
    )


def test_committed_report_recovery_skips_collection_and_preserves_profile(
    intelligence_app, project_root, monkeypatch
):
    app, client, project, calls = intelligence_app
    _, report = run_scan(client, project)
    repo = app.state.deep_dive_manager.repository(project)
    original_profile = repo.profile_for_scan(report["scan_id"])
    count = len(calls)

    async def forbidden_collection(*args, **kwargs):
        raise AssertionError("Committed report must not recollect or overwrite its scan profile")

    monkeypatch.setattr(SignalHarnessWorkflow, "_collect_watchlist", forbidden_collection)
    workflow = SignalHarnessWorkflow(
        cwd=project_root,
        config_dir=project_root / "configs",
        state_dir=repo.path.parent,
        output_dir=repo.path.parent / "recovery-outputs",
        project_id=project,
        mode="agent",
        intelligence_pipeline=True,
    )
    result = asyncio.run(workflow.scan(scan_id=report["scan_id"], window_mode="24h"))
    assert result.scan_id == report["scan_id"]
    assert result.window.public_payload() == report["window"]
    assert len(result.signals) == report["counts"]["observations"]
    assert repo.profile_for_scan(report["scan_id"]) == original_profile
    assert repo.report(report["scan_id"]) == report
    assert len(calls) == count


def test_mvp_synthesis_provider_order_prefers_kimi_then_qwen(project_root, monkeypatch):
    for prefix in ("QWEN", "KIMI", "DEEPSEEK"):
        monkeypatch.setenv(f"{prefix}_API_KEY", "test-only-secret")
        monkeypatch.setenv(f"{prefix}_BASE_URL", "https://example.com/v1")
    policy = TaskPolicy.load(project_root / "configs")
    assert policy.providers("synthesis") == ["kimi", "qwen"]


def test_activity_summary_endpoint_is_project_scoped_and_model_free(intelligence_app):
    _, client, project, calls = intelligence_app
    _, report = run_scan(client, project)
    before = len(calls)
    base = f"/intelligence/projects/{project}/reports/{report['scan_id']}"
    response = client.get(base + "/activity-summary")
    assert response.status_code == 200
    assert response.json() == {
        "total_count": 0,
        "groups": [],
        "other_count": 0,
        "type_counts": {},
    }
    assert len(calls) == before
    assert (
        client.get(
            f"/intelligence/projects/missing-project/reports/{report['scan_id']}/activity-summary"
        ).status_code
        == 404
    )


def test_current_change_feedback_and_outcome_share_durable_calibration(intelligence_app):
    _, client, project, calls = intelligence_app
    _, report = run_scan(client, project)
    base = f"/intelligence/projects/{project}/reports/{report['scan_id']}"
    change = client.get(base + "/changes?view=relevant&limit=1").json()["items"][0]
    path = f"{base}/changes/{change['change_id']}"
    before_report = client.get(base).json()
    before_calls = len(calls)

    feedback = client.post(
        path + "/feedback",
        json={"label": "useful", "note": "current product feedback"},
    )
    assert feedback.status_code == 201, feedback.text
    assert feedback.json()["change_id"] == change["change_id"]
    assert feedback.json()["policy_applied"] is False

    outcome = client.post(
        path + "/outcome",
        json={"impact_observed": True, "note": "confirmed after review"},
    )
    assert outcome.status_code == 201, outcome.text
    assert outcome.json()["change_id"] == change["change_id"]

    calibration = client.get(f"/intelligence/projects/{project}/calibration").json()
    assert calibration["feedback_count"] == 1
    assert calibration["outcome_count"] == 1
    assert calibration["episode_count"] >= 1
    assert calibration["ready_for_replay"] is False
    assert calibration["feedback_labels"] == {"useful": 1}
    assert calibration["labels_needed"] == 2
    assert calibration["learning_state"] == "collecting"
    assert calibration["candidate"]["proposal_id"]
    assert calibration["candidate_replay"]["recommendation"] == "insufficient_evidence"
    assert calibration["candidate_replay"]["promotion_allowed"] is False
    assert calibration["staged_proposals"] == []
    assert calibration["policy_revisions"] == []
    assert client.get(base).json() == before_report
    assert len(calls) == before_calls  # feedback/evolution writes never invoke the report models.


def test_learning_read_model_keeps_staged_candidate_blocked_without_durable_gate(
    intelligence_app,
):
    app, client, project, calls = intelligence_app
    application = app.state.environment_application
    state = prepare_project_state(
        application.state_dir,
        project,
        migrate_legacy_default=True,
    )
    save_learning_staging(
        state,
        [
            StagedLearningProposal(
                proposal_id="proposal-staged-but-not-promotable",
                status="staged",
                created_at="2026-09-13T00:00:00+00:00",
                risk=ProposalRiskReport(
                    risk_level="low",
                    reasons=["offline staging exists"],
                    auto_stage_allowed=True,
                    apply_requires_approval=False,
                    replay_gate_passed=True,
                ),
                learning={},
            )
        ],
    )
    atomic_write_text(
        state / "calibration_replay.json",
        json.dumps(
            {
                "recommendation": "insufficient_evidence",
                "promotion_allowed": False,
            }
        )
        + "\n",
    )
    before_calls = len(calls)

    status = client.get(f"/intelligence/projects/{project}/calibration")
    assert status.status_code == 200
    payload = status.json()
    assert payload["learning_state"] == "candidate_blocked"
    assert payload["staged_proposals"][0]["status"] == "staged"
    assert payload["durable_replay"]["promotion_allowed"] is False
    assert len(calls) == before_calls


@pytest.mark.asyncio
async def test_current_mcp_reads_same_environment_truth_as_rest(intelligence_app):
    app, client, project, calls = intelligence_app
    _, report = run_scan(client, project)
    application = app.state.environment_application
    server = build_mcp_server(
        cwd=application.cwd,
        config_dir=application.config_dir,
        output_dir=application.output_dir,
        state_dir=application.state_dir,
        stream_manager=app.state.stream_manager,
        environment_application=application,
    )
    base = f"/intelligence/projects/{project}/reports/{report['scan_id']}"
    rest_changes = client.get(base + "/changes?view=relevant&limit=3").json()
    before_calls = len(calls)

    async with Client(server) as mcp_client:
        mcp_report = await mcp_client.call_tool(
            "signalharness_get_environment_report",
            {"project_id": project, "scan_id": report["scan_id"]},
        )
        assert mcp_report.is_error is False
        mcp_report_payload = mcp_report.structured_content or {}
        assert mcp_report_payload["scan_id"] == report["scan_id"]
        assert mcp_report_payload["directions"] == report["directions"]
        assert mcp_report_payload["radar"] == report["radar"]

        mcp_changes = await mcp_client.call_tool(
            "signalharness_list_environment_changes",
            {
                "project_id": project,
                "scan_id": report["scan_id"],
                "view": "relevant",
                "limit": 3,
            },
        )
        assert mcp_changes.is_error is False
        mcp_change_payload = mcp_changes.structured_content or {}
        assert [item["change_id"] for item in mcp_change_payload["items"]] == [
            item["change_id"] for item in rest_changes["items"]
        ]

        context = await mcp_client.call_tool(
            "signalharness_get_project_context", {"project_id": project}
        )
        assert context.is_error is False
        context_payload = context.structured_content or {}
        rest_profile = client.get(f"/projects/{project}/profile").json()
        assert context_payload["profile_revision_id"] == rest_profile["profile_revision_id"]
        assert context_payload["project_profile"] == rest_profile["effective_profile"]

    assert len(calls) == before_calls


def test_current_cli_reads_same_environment_truth_as_rest(intelligence_app):
    app, client, project, calls = intelligence_app
    _, report = run_scan(client, project)
    application = app.state.environment_application
    runner = CliRunner()
    common = [
        "--project",
        project,
        "--cwd",
        str(application.cwd),
        "--config-dir",
        str(application.config_dir),
        "--state-dir",
        str(application.state_dir),
    ]
    before_calls = len(calls)

    cli_report = runner.invoke(
        cli_app, ["environment-report", "--scan", report["scan_id"], *common]
    )
    assert cli_report.exit_code == 0, cli_report.output
    cli_report_payload = json.loads(cli_report.output)
    assert cli_report_payload["scan_id"] == report["scan_id"]
    assert cli_report_payload["directions"] == report["directions"]
    assert cli_report_payload["radar"] == report["radar"]

    cli_changes = runner.invoke(
        cli_app,
        [
            "environment-changes",
            "--scan",
            report["scan_id"],
            "--view",
            "relevant",
            "--limit",
            "2",
            *common,
        ],
    )
    assert cli_changes.exit_code == 0, cli_changes.output
    cli_change_payload = json.loads(cli_changes.output)
    rest_changes = client.get(
        f"/intelligence/projects/{project}/reports/{report['scan_id']}/changes?view=relevant&limit=2"
    ).json()
    assert [item["change_id"] for item in cli_change_payload["items"]] == [
        item["change_id"] for item in rest_changes["items"]
    ]

    cli_context = runner.invoke(cli_app, ["environment-context", *common])
    assert cli_context.exit_code == 0, cli_context.output
    cli_context_payload = json.loads(cli_context.output)
    rest_context = client.get(f"/projects/{project}/profile").json()
    assert cli_context_payload["profile_revision_id"] == rest_context["profile_revision_id"]
    assert cli_context_payload["effective_profile"] == rest_context["effective_profile"]
    assert len(calls) == before_calls
