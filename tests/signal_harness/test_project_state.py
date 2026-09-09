from __future__ import annotations

from datetime import datetime, timezone

from signal_harness.signal.project_state import resolve_project_change_state
from signal_harness.signal.schemas import SignalEvent


def _event(**updates: object) -> SignalEvent:
    payload: dict[str, object] = {
        "event_id": "state-001",
        "source_type": "github_release",
        "source_name": "mcp",
        "title": "MCP release",
        "content": "Transport and schema compatibility changes.",
        "url": "https://example.com/change",
        "published_at": datetime.now(timezone.utc),
        "current_version": "2.2.0",
        "raw_payload": {"official": True},
        "collected_at": datetime.now(timezone.utc),
    }
    payload.update(updates)
    return SignalEvent.model_validate(payload)


def _profile() -> dict[str, object]:
    return {
        "dependencies": ["mcp", "fastapi"],
        "protocols": ["Model Context Protocol"],
        "dependency_evidence": [
            {"name": "mcp", "resolved_version": "2.1.1"},
            {"name": "fastapi", "resolved_version": "0.141.1"},
        ],
    }


def test_project_state_uses_source_identity_not_body_mentions() -> None:
    event = _event(
        source_name="openai/openai-agents-python",
        current_version="0.20.0",
        content="This SDK release mentions an MCP dependency migration.",
    )
    state = resolve_project_change_state(event, _profile())
    assert state.direct_dependency is False
    assert state.protocol_name is None


def test_project_state_compares_release_to_resolved_version() -> None:
    newer = resolve_project_change_state(_event(), _profile())
    superseded = resolve_project_change_state(
        _event(source_name="fastapi", current_version="0.140.13"),
        _profile(),
    )
    installed = resolve_project_change_state(
        _event(source_name="fastapi", current_version="0.141.1"),
        _profile(),
    )
    assert newer.version_relation == "newer"
    assert superseded.version_relation == "superseded"
    assert superseded.already_satisfied is True
    assert installed.version_relation == "installed"
    assert installed.already_satisfied is True


def test_project_state_recognizes_issue_fixed_on_installed_major() -> None:
    event = _event(
        source_type="github_issue",
        current_version=None,
        title="v1 Streamable HTTP backport",
        content="The request bug is already fixed on 2.x and needs a v1 backport.",
    )
    state = resolve_project_change_state(event, _profile())
    assert state.direct_dependency is True
    assert state.issue_fixed_for_installed_major is True
    assert state.already_satisfied is True


def test_protocol_identity_is_resolved_without_making_it_a_package_release() -> None:
    event = _event(
        source_type="web_change",
        source_name="Model Context Protocol",
        current_version=None,
        title="Protocol sessions removed",
    )
    state = resolve_project_change_state(event, _profile())
    assert state.protocol_name == "Model Context Protocol"
    assert state.direct_dependency is False


def test_project_state_compares_package_registry_release_to_installed_version() -> None:
    event = _event(
        source_type="package_registry",
        source_name="mcp",
        current_version="2.2.0",
        raw_payload={"package_name": "mcp", "registry": "pypi", "official": True},
    )
    state = resolve_project_change_state(event, _profile())
    assert state.dependency_name == "mcp"
    assert state.resolved_version == "2.1.1"
    assert state.version_relation == "newer"
    assert state.newer_direct_release is True
