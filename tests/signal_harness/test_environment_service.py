from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from signal_harness.intelligence.usage import inspect_usage
from signal_harness.providers.task_policy import TaskPolicy
from signal_harness.runtime.workflow import CollectionBatch, SignalHarnessWorkflow
from signal_harness.service import create_app
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


def test_task_policy_honors_new_model_pin_without_changing_legacy_env(project_root, monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-only-secret")
    monkeypatch.setenv("DEEPSEEK_BASE_URL", "https://example.com/v1")
    monkeypatch.setenv("DEEPSEEK_MODEL", "deepseek-v4-flash")
    provider = TaskPolicy.load(project_root / "configs").create_provider("deepseek", "synthesis")
    assert provider.model == "deepseek-v4-pro"
    assert provider.profile.max_input_tokens > 100000
    assert provider.request_options == {"thinking": {"type": "enabled"}, "reasoning_effort": "high"}
    asyncio.run(provider.close())


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
