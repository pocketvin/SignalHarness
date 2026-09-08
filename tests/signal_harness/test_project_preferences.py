from __future__ import annotations

from pathlib import Path

from signal_harness.persistence import ChangeLedger
from signal_harness.projects.preferences import (
    Importance,
    PreferenceInput,
    PreferenceScope,
    parse_preference_instruction,
)
from signal_harness.signal.candidates import candidate_score
from signal_harness.signal.normalizer import normalize_event
from signal_harness.signal.policy import load_signal_policy


def _profile() -> dict:
    return {
        "project_name": "demo",
        "goal": "test",
        "tech_stack": ["Python"],
        "critical_modules": ["MCP integration"],
        "dependencies": ["mcp", "react"],
        "monitored_ecosystem": ["React ecosystem"],
        "providers": [],
        "runtimes": ["Python"],
        "protocols": ["Model Context Protocol"],
        "competitors": [],
        "focus_keywords": [],
        "ignore_keywords": [],
    }

def test_natural_language_preferences_map_to_structured_state() -> None:
    profile = _profile()

    mcp = parse_preference_instruction("MCP 很重要", profile)
    react = parse_preference_instruction("不要关注 React", profile)
    n8n = parse_preference_instruction("add n8n/API", profile)

    assert mcp.scope_type is PreferenceScope.DEPENDENCY
    assert mcp.scope_key == "mcp"
    assert mcp.importance is Importance.IMPORTANT
    assert react.scope_type is PreferenceScope.DEPENDENCY
    assert react.scope_key == "react"
    assert react.importance is Importance.IGNORE
    assert n8n.scope_type is PreferenceScope.TOPIC
    assert n8n.scope_key == "n8n/api"
    assert n8n.importance is Importance.IMPORTANT


def test_profile_revisions_preserve_explicit_preference_history(tmp_path: Path) -> None:
    ledger = ChangeLedger(tmp_path / "ledger.sqlite3")
    first = ledger.ensure_profile_revision(project_id="demo", auto_profile=_profile())
    preference = ledger.set_preference(
        project_id="demo",
        preference=PreferenceInput(
            scope_type=PreferenceScope.DEPENDENCY,
            scope_key="mcp",
            importance=Importance.CRITICAL,
            source="ui",
        ),
    )
    second = ledger.ensure_profile_revision(project_id="demo", auto_profile=_profile())

    assert first["profile_revision_id"] != second["profile_revision_id"]
    assert second["preferences"][0]["preference_id"] == preference["preference_id"]
    assert second["effective_profile"]["importance_preferences"][0]["importance"] == "critical"

def test_scan_pins_profile_revision_even_after_preference_changes(tmp_path: Path) -> None:
    ledger = ChangeLedger(tmp_path / "ledger.sqlite3")
    first = ledger.ensure_profile_revision(project_id="demo", auto_profile=_profile())
    ledger.begin_scan(
        scan_id="scan-one",
        project_id="demo",
        collected_count=0,
        deduped_count=0,
        profile_revision_id=first["profile_revision_id"],
    )
    ledger.set_preference(
        project_id="demo",
        preference=PreferenceInput(
            scope_type=PreferenceScope.DEPENDENCY,
            scope_key="react",
            importance=Importance.IGNORE,
        ),
    )
    second = ledger.ensure_profile_revision(project_id="demo", auto_profile=_profile())

    assert second["profile_revision_id"] != first["profile_revision_id"]
    assert ledger.scan_profile_revision_id(scan_id="scan-one") == first["profile_revision_id"]


def test_importance_preference_changes_candidate_ranking(project_root: Path) -> None:
    event = normalize_event(
        {
            "source_type": "github_release",
            "source_name": "modelcontextprotocol/python-sdk",
            "title": "MCP transport compatibility release",
            "content": "Protocol compatibility and tool transport changes.",
            "url": "https://example.test/mcp-release",
            "raw_payload": {"source_authority": "official"},
        }
    )
    policy = load_signal_policy(project_root / "configs/signal_policy.yaml")
    base = _profile()
    critical = {**base, "importance_preferences": [{"scope_key": "mcp", "importance": "critical"}]}
    ignored = {**base, "importance_preferences": [{"scope_key": "mcp", "importance": "ignore"}]}

    critical_score = candidate_score(event, critical, policy)
    ignored_score = candidate_score(event, ignored, policy)

    assert critical_score > ignored_score
    assert critical_score - ignored_score >= 20

def test_profile_preference_rest_round_trip(project_root: Path, tmp_path: Path) -> None:
    from fastapi.testclient import TestClient

    from signal_harness.service import create_app

    app = create_app(
        cwd=project_root,
        output_dir=tmp_path / "outputs",
        state_dir=tmp_path / "state",
    )
    with TestClient(app) as client:
        initial = client.get("/projects/signalharness/profile")
        assert initial.status_code == 200
        initial_payload = initial.json()
        created = client.post(
            "/projects/signalharness/preferences",
            json={
                "scope_type": "protocol",
                "scope_key": "MCP",
                "importance": "critical",
                "note": "Core integration path",
            },
        )
        assert created.status_code == 200
        created_payload = created.json()
        assert created_payload["profile_revision_id"] != initial_payload["profile_revision_id"]
        assert created_payload["preferences"][-1]["importance"] == "critical"
        preference_id = created_payload["preferences"][-1]["preference_id"]

        natural = client.post(
            "/projects/signalharness/preferences/natural-language",
            json={"instruction": "MCP 很重要"},
        )
        assert natural.status_code == 200
        natural_payload = natural.json()
        assert any(item["source"] == "natural_language" for item in natural_payload["preferences"])

        revoked = client.delete(
            f"/projects/signalharness/preferences/{preference_id}"
        )
        assert revoked.status_code in {200, 404}

def test_auto_profile_refresh_preserves_explicit_override(tmp_path: Path) -> None:
    ledger = ChangeLedger(tmp_path / "ledger.sqlite3")
    ledger.set_preference(
        project_id="demo",
        preference=PreferenceInput(
            scope_type=PreferenceScope.DEPENDENCY,
            scope_key="mcp",
            importance=Importance.CRITICAL,
            source="ui",
        ),
    )
    refreshed = _profile()
    refreshed["purpose"] = "A newly discovered purpose"
    refreshed["dependencies"] = ["mcp", "react", "n8n"]

    revision = ledger.ensure_profile_revision(project_id="demo", auto_profile=refreshed)

    assert revision["auto_profile"]["purpose"] == "A newly discovered purpose"
    assert revision["effective_profile"]["dependencies"] == ["mcp", "react", "n8n"]
    assert revision["effective_profile"]["importance_preferences"][0]["scope_key"] == "mcp"
    assert revision["effective_profile"]["importance_preferences"][0]["importance"] == "critical"

def test_run_metadata_pins_profile_revision_across_later_preference_changes(
    project_root: Path, tmp_path: Path
) -> None:
    from fastapi.testclient import TestClient

    from signal_harness.service import create_app

    app = create_app(
        cwd=project_root,
        output_dir=tmp_path / "outputs",
        state_dir=tmp_path / "state",
    )
    with TestClient(app) as client:
        updated = client.post(
            "/projects/signalharness/preferences",
            json={
                "scope_type": "protocol",
                "scope_key": "MCP",
                "importance": "critical",
            },
        ).json()
        pinned = updated["profile_revision_id"]
        run_response = client.post(
            "/runs",
            json={
                "project_id": "signalharness",
                "data_source": "fixture",
                "mode": "demo",
                "fixture": "examples/signal_harness/sample_events.json",
                "max_events": 2,
            },
        )
        assert run_response.status_code == 201, run_response.text
        run = run_response.json()
        assert run["profile_revision_id"] == pinned

        later = client.post(
            "/projects/signalharness/preferences",
            json={
                "scope_type": "protocol",
                "scope_key": "MCP",
                "importance": "low",
            },
        ).json()
        assert later["profile_revision_id"] != pinned

        stored = client.get(f"/runs/{run['run_id']}").json()
        assert stored["profile_revision_id"] == pinned


def test_preferences_are_project_scoped(tmp_path: Path) -> None:
    ledger = ChangeLedger(tmp_path / "ledger.sqlite3")
    ledger.set_preference(
        project_id="project-a",
        preference=PreferenceInput(
            scope_type=PreferenceScope.DEPENDENCY,
            scope_key="mcp",
            importance=Importance.CRITICAL,
            source="ui",
        ),
    )
    profile_a = ledger.ensure_profile_revision(project_id="project-a", auto_profile=_profile())
    profile_b = ledger.ensure_profile_revision(project_id="project-b", auto_profile=_profile())

    assert profile_a["preferences"][0]["scope_key"] == "mcp"
    assert profile_b["preferences"] == []
    assert profile_b["effective_profile"].get("importance_preferences", []) == []
