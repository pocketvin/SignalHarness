from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from signal_harness.agents.classifier import ClassifierAgent
from signal_harness.projects.onboarding import ProjectManifest, draft_project
from signal_harness.runtime.permissions import SignalPermissionGuard
from signal_harness.runtime.tools_base import ToolExecutionContext, ToolResult
from signal_harness.runtime.workflow import SignalHarnessWorkflow
from signal_harness.signal.normalizer import normalize_event
from signal_harness.signal.project_state import resolve_project_change_state
from signal_harness.signal.schemas import SignalCategory
from signal_harness.signal.scorer import relevance_score
from signal_harness.tools.rss_signal import RssSignalTool


def _bound_event(entity_type: str, entity_name: str):
    return normalize_event(
        {
            "source_type": "web_change",
            "source_name": "Official changelog",
            "title": "Behavior update",
            "content": "Clarified behavior and compatibility details.",
            "url": "https://example.com/changelog",
            "published_at": "2026-09-09T00:00:00Z",
            "official": True,
            "entity_type": entity_type,
            "entity_name": entity_name,
        }
    )


def test_bound_provider_matches_only_when_profile_uses_provider() -> None:
    event = _bound_event("provider", "OpenAI API")
    used_profile = {"providers": ["OpenAI API"]}
    unused_profile = {"providers": []}

    used = resolve_project_change_state(event, used_profile)
    unused = resolve_project_change_state(event, unused_profile)

    assert used.provider_name == "OpenAI API"
    assert unused.provider_name is None
    assert relevance_score(event, used_profile) > relevance_score(event, unused_profile)
    classification = ClassifierAgent().run(event, used_profile)
    assert classification.category == SignalCategory.PROVIDER_COMPATIBILITY_SIGNAL


def test_bound_dependency_and_protocol_do_not_require_body_keyword_match() -> None:
    dependency_event = _bound_event("dependency", "fastapi")
    dependency_profile = {"dependencies": ["fastapi"]}
    dependency_state = resolve_project_change_state(dependency_event, dependency_profile)
    assert dependency_state.dependency_name == "fastapi"
    assert dependency_state.direct_dependency is True

    protocol_event = _bound_event("protocol", "Model Context Protocol")
    protocol_profile = {"protocols": ["Model Context Protocol"]}
    protocol_state = resolve_project_change_state(protocol_event, protocol_profile)
    assert protocol_state.protocol_name == "Model Context Protocol"
    classification = ClassifierAgent().run(protocol_event, protocol_profile)
    assert classification.category == SignalCategory.AGENT_RUNTIME_SIGNAL


def test_bound_runtime_is_exact_and_missing_profile_usage_does_not_match() -> None:
    event = _bound_event("runtime", "ASGI")
    assert resolve_project_change_state(event, {"runtimes": ["ASGI"]}).runtime_name == "ASGI"
    assert resolve_project_change_state(event, {"runtimes": ["Node.js"]}).runtime_name is None


def test_onboarding_generates_usage_bound_official_changelog_sources() -> None:
    draft = draft_project(
        manifests=[
            ProjectManifest(
                path="pyproject.toml",
                content=(
                    "[project]\n"
                    "name='bound-demo'\n"
                    "dependencies=['fastapi>=0.116','openai>=2','mcp>=2']\n"
                ),
            )
        ],
        paths=["src/api.py"],
    )

    assert draft.project_profile["providers"] == ["OpenAI API"]
    sources = {item["url"]: item for item in draft.watchlist["web_changes"]["sources"]}
    assert sources["https://developers.openai.com/api/docs/changelog"]["entity_type"] == "provider"
    assert sources["https://developers.openai.com/api/docs/changelog"]["entity_name"] == "OpenAI API"
    assert sources["https://modelcontextprotocol.io/specification/latest"]["entity_type"] == "protocol"
    assert sources["https://modelcontextprotocol.io/specification/latest"]["entity_name"] == "Model Context Protocol"


@pytest.mark.asyncio
async def test_rss_watchlist_entity_binding_reaches_normalized_event(
    project_root: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_rss_execute(
        self: RssSignalTool,
        arguments: Any,
        context: ToolExecutionContext,
    ) -> ToolResult:
        del self, arguments, context
        return ToolResult(
            output=json.dumps(
                [
                    {
                        "title": "API behavior update",
                        "summary": "A compatibility change was published.",
                        "link": "https://example.com/item",
                        "published": "2026-09-09T00:00:00Z",
                    }
                ]
            ),
            metadata={
                "coverage_status": "complete",
                "history_limited": False,
                "pages_fetched": 1,
                "diagnostics": [],
            },
        )

    monkeypatch.setattr(RssSignalTool, "execute", fake_rss_execute)
    workflow = SignalHarnessWorkflow(
        cwd=project_root,
        config_dir=project_root / "configs",
        output_dir=tmp_path / "outputs",
        state_dir=tmp_path / "state",
    )
    watchlist = {
        "rss": {
            "feeds": [
                {
                    "name": "Provider changelog",
                    "official": True,
                    "url": "https://example.com/feed.xml",
                    "entity_type": "provider",
                    "entity_name": "OpenAI API",
                }
            ]
        }
    }
    collection = await workflow._collect_watchlist(  # noqa: SLF001
        watchlist,
        since=None,
        guard=SignalPermissionGuard(),
        profile={"providers": ["OpenAI API"]},
    )

    assert len(collection.events) == 1
    event = workflow._normalize_collected(collection.events[0])  # noqa: SLF001
    assert event.raw_payload["entity_type"] == "provider"
    assert event.raw_payload["entity_name"] == "OpenAI API"
    assert resolve_project_change_state(event, {"providers": ["OpenAI API"]}).provider_name == "OpenAI API"
