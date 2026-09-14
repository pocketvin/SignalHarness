from __future__ import annotations

from datetime import datetime, timezone

import pytest

from signal_harness.intelligence.contracts import Evidence, ProductChange, RadarItem, SynthesisOutput
from signal_harness.intelligence.project_projection import (
    build_project_references,
    model_row_to_insight,
)
from signal_harness.intelligence.contracts import ChangeDigest, ShallowModelRow
from signal_harness.intelligence.quality import validate_synthesis_semantics
from signal_harness.projects.discovery_profile import derive_discovery_profile


def test_game_discovery_profile_does_not_inherit_incidental_agent_modules() -> None:
    profile = derive_discovery_profile(
        {
            "project_name": "magic-kitchen",
            "purpose": "Magic Kitchen WeChat mini game",
            "critical_modules": ["Agent orchestration", "frontend", "testing"],
            "tech_stack": ["TypeScript / JavaScript"],
        }
    )
    text = " ".join(
        [
            profile["project_domain"],
            *profile["problem_spaces"],
            *profile["solution_categories"],
            *profile["discovery_queries"],
        ]
    ).casefold()
    assert profile["project_domain"] == "小游戏 / 互动产品"
    assert "agent runtime" not in text
    assert "llm" not in text
    assert "mcp" not in text
    assert all("game" in query.casefold() for query in profile["discovery_queries"])
    assert all("readme" not in query.casefold() for query in profile["discovery_queries"])
    assert "LLM agent" in profile["exclusions"]



def test_unknown_purpose_does_not_promote_incidental_agent_module_to_ai_radar() -> None:
    profile = derive_discovery_profile(
        {
            "purpose": "Software project demo; purpose not declared in a supported manifest.",
            "critical_modules": ["Agent orchestration", "testing"],
            "tech_stack": ["TypeScript / JavaScript"],
        }
    )
    text = " ".join(profile["discovery_queries"]).casefold()
    assert profile["project_domain"] == "通用软件工程"
    assert "agent" not in text
    assert "mcp" not in text
    assert "llm" not in text

def test_ai_gateway_discovery_profile_is_conditioned_on_its_real_problem_space() -> None:
    profile = derive_discovery_profile(
        {
            "purpose": "Multi-channel AI gateway with extensible messaging integrations",
            "protocols": ["Model Context Protocol"],
            "critical_modules": ["Agent orchestration", "provider adapter"],
        }
    )
    assert profile["project_domain"] == "AI / Agent 开发工具"
    assert "Agent 执行与工具互操作" in profile["problem_spaces"]
    assert any("agent runtime" in value for value in profile["discovery_queries"])
    assert any("MCP" in value for value in profile["solution_categories"])


def _change(
    change_id: str,
    *,
    entity: str,
    source: str,
    discovered: bool,
) -> ProductChange:
    return ProductChange(
        change_id=change_id,
        revision_id=f"rev-{change_id}",
        title=f"{entity} 新方案",
        entity=entity,
        kind="github_repository" if discovered else "github_release",
        published_at="2026-09-12T00:00:00+00:00",
        discovery_origin="discovered" if discovered else "watched",
        discovery_basis='"agent runtime" tools' if discovered else "",
        summary=f"{entity} 出现新的公开方案。",
        what_changed=f"{entity} 发布了与项目问题空间相关的新方案。",
        project_relation="context",
        relation_reason="问题空间：Agent 执行与工具互操作。",
        attention="normal",
        topics=["工具互操作"],
        uncertainty="仍需继续观察采用情况。",
        interpretation_status="ready",
        relevant=True,
        evidence=[
            Evidence(
                evidence_id=f"ev-{change_id}",
                event_revision_id=int(change_id.removeprefix("c")),
                source_name=source,
                source_type="github_repository" if discovered else "github_release",
                url=f"https://github.com/{source}",
                authority="maintainer" if discovered else "official",
                excerpt="维护者公开仓库说明。",
            )
        ],
    )


def test_new_solution_radar_allows_one_discovered_entity_but_not_trend_claims() -> None:
    discovered = _change("c1", entity="acme/tool-sandbox", source="acme/tool-sandbox", discovered=True)
    base = SynthesisOutput(
        brief=[{"text": "这期出现了新的相邻方案。", "supporting_change_ids": ["c1"]}],
        radar=[
            RadarItem(
                radar_type="new_solution",
                title="一个新的工具隔离方案出现",
                explanation="它直接面向工具执行隔离，和当前问题空间有交集。",
                supporting_change_ids=["c1"],
                project_connection="如果后续需要更严格的执行边界，可以把它作为候选方案对照。",
                why_now="这个仓库本期第一次进入项目雷达。",
            )
        ],
    )
    validate_synthesis_semantics(base, {"c1": discovered})

    bad = base.model_copy(deep=True)
    bad.radar[0].explanation = "这个方向正在升温，值得立即跟进。"
    with pytest.raises(ValueError, match="single new solution"):
        validate_synthesis_semantics(bad, {"c1": discovered})


def test_emerging_radar_requires_independent_discovered_entities() -> None:
    first = _change("c1", entity="acme/tool-sandbox", source="acme/tool-sandbox", discovered=True)
    second = _change("c2", entity="beta/execution-box", source="beta/execution-box", discovered=True)
    output = SynthesisOutput(
        brief=[{"text": "相邻方案开始出现共同结构。", "supporting_change_ids": ["c1", "c2"]}],
        radar=[
            RadarItem(
                radar_type="emerging_direction",
                title="工具执行隔离开始形成独立方案层",
                explanation="两个独立团队都把执行隔离做成单独组件。",
                supporting_change_ids=["c1", "c2"],
                project_connection="这和项目的工具执行边界属于同一层问题。",
                why_now="本期同时发现两个此前未跟踪的独立实现。",
            )
        ],
    )
    validate_synthesis_semantics(output, {"c1": first, "c2": second})

    bad = output.model_copy(deep=True)
    bad.radar[0].supporting_change_ids = ["c1"]
    with pytest.raises(ValueError, match="Emerging radar direction"):
        validate_synthesis_semantics(bad, {"c1": first, "c2": second})

    overclaim = output.model_copy(deep=True)
    overclaim.radar[0].title = "工具执行隔离已经成为标配"
    with pytest.raises(ValueError, match="adoption prevalence"):
        validate_synthesis_semantics(overclaim, {"c1": first, "c2": second})


def test_discovery_only_brief_cannot_claim_adoption_prevalence() -> None:
    first = _change("c1", entity="acme/model-gateway", source="acme/model-gateway", discovered=True)
    second = _change("c2", entity="beta/provider-router", source="beta/provider-router", discovered=True)
    output = SynthesisOutput(
        brief=[
            {
                "text": "多 Provider 网关的故障转移能力正在被广泛复刻。",
                "supporting_change_ids": ["c1", "c2"],
            }
        ],
        radar=[],
    )
    with pytest.raises(ValueError, match="Discovery-only brief"):
        validate_synthesis_semantics(output, {"c1": first, "c2": second})


def test_discovery_problem_space_is_a_context_only_project_reference() -> None:
    profile = {
        "purpose": "Agent tool runtime",
        "discovery_profile": {
            "version": "project-discovery-v1",
            "project_domain": "AI / Agent 开发工具",
            "problem_spaces": ["Agent 执行与工具互操作"],
            "solution_categories": ["工具协议与沙箱"],
        },
    }
    refs = {item.ref_id: item for item in build_project_references(profile)}
    basis_id = next(key for key, value in refs.items() if value.label == "Agent 执行与工具互操作")
    assert refs[basis_id].kind == "discovery"
    digest = ChangeDigest(
        change_id="c1",
        revision_id="r1",
        title="A new tool execution sandbox",
        entity="acme/sandbox",
        kind="github_repository",
        published_at=datetime.now(timezone.utc).isoformat(),
        discovery_origin="discovered",
        discovery_basis='"agent runtime" tools',
        evidence=[
            Evidence(
                evidence_id="e1",
                event_revision_id=1,
                source_name="acme/sandbox",
                source_type="github_repository",
                url="https://github.com/acme/sandbox",
                authority="maintainer",
                excerpt="Tool execution sandbox for agents.",
            )
        ],
    )
    insight = model_row_to_insight(
        ShallowModelRow(
            id="c1",
            s="新的工具执行隔离方案出现",
            f="维护者公开了一个面向工具执行隔离的新仓库。",
            r="direct",
            b=basis_id,
            n="和这一问题空间有直接交集",
            a="normal",
            t=["工具隔离"],
            e=["e1"],
        ),
        references=refs,
        digest=digest,
    )
    assert insight.project_relation == "context"
    assert "问题空间" in insight.relation_reason


@pytest.mark.asyncio
async def test_workflow_discovery_accepts_unknown_repo_and_filters_non_ai_exclusions(
    project_root,
    tmp_path,
    monkeypatch,
) -> None:
    import json

    from signal_harness.agent_integration.mode import RunMode
    from signal_harness.runtime.permissions import SignalPermissionGuard
    from signal_harness.runtime.tools_base import ToolResult
    from signal_harness.runtime.workflow import SignalHarnessWorkflow
    from signal_harness.tools.github_signal import GitHubSignalTool

    async def fake_execute(self, arguments, context):
        del self, context
        if arguments.action != "search_repositories":
            return ToolResult(is_error=False, output="[]", metadata={"coverage_status": "complete"})
        row = {
            "id": 1,
            "full_name": "acme/ai-agent-kit",
            "name": "ai-agent-kit",
            "description": "LLM agent runtime toolkit",
            "html_url": "https://github.com/acme/ai-agent-kit",
            "created_at": "2026-09-10T00:00:00Z",
            "updated_at": "2026-09-10T00:00:00Z",
            "topics": ["agent"],
            "discovery_origin": "discovered",
            "discovery_basis": arguments.query,
        }
        return ToolResult(
            is_error=False,
            output=json.dumps([row]),
            metadata={
                "coverage_status": "unknown",
                "history_limited": False,
                "pages_fetched": 1,
                "diagnostics": [],
            },
        )

    monkeypatch.setattr(GitHubSignalTool, "execute", fake_execute)
    workflow = SignalHarnessWorkflow(
        cwd=project_root,
        config_dir=project_root / "configs",
        output_dir=tmp_path / "outputs",
        state_dir=tmp_path / "state",
        mode=RunMode.DEMO,
        project_id="discovery-test",
        intelligence_pipeline=True,
    )
    guard = SignalPermissionGuard()

    ai_profile = {
        "purpose": "Multi-channel AI gateway",
        "discovery_profile": derive_discovery_profile({"purpose": "Multi-channel AI gateway"}),
    }
    ai_batch = await workflow._collect_watchlist({}, since=None, guard=guard, profile=ai_profile)
    assert len(ai_batch.events) == 1
    assert ai_batch.events[0]["_collector_source_name"] == "acme/ai-agent-kit"

    game_profile = {
        "purpose": "Magic Kitchen WeChat mini game",
        "discovery_profile": derive_discovery_profile(
            {
                "purpose": "Magic Kitchen WeChat mini game",
                "critical_modules": ["Agent orchestration", "frontend"],
            }
        ),
    }
    game_batch = await workflow._collect_watchlist({}, since=None, guard=guard, profile=game_profile)
    assert game_batch.events == []
    assert game_batch.failed_sources == []
    assert all(task.source_type == "github_repository" for task in game_batch.source_tasks)


def test_discovery_source_failure_does_not_degrade_core_coverage_or_checkpoint() -> None:
    from signal_harness.runtime.workflow import SignalHarnessWorkflow
    from signal_harness.signal.schemas import SourceTask

    tasks = [
        SourceTask(
            task_id="core",
            source_name="tracked/repo",
            source_type="github_release",
            status="success",
            coverage_status="complete",
        ),
        SourceTask(
            task_id="radar",
            source_name="GitHub Discovery · 1",
            source_type="github_repository",
            status="failed",
            coverage_status="unknown",
            error="search unavailable",
        ),
    ]
    assert SignalHarnessWorkflow._coverage_status(tasks, []) == "complete"
    assert SignalHarnessWorkflow._checkpoint_safe(tasks, []) is True


def test_discovered_repository_outside_source_window_surfaces_only_once(
    project_root,
    tmp_path,
) -> None:
    from datetime import timedelta

    from signal_harness.agent_integration.mode import RunMode
    from signal_harness.runtime.windows import ResolvedScanWindow
    from signal_harness.runtime.workflow import SignalHarnessWorkflow
    from signal_harness.signal.schemas import SignalEvent

    now = datetime.now(timezone.utc)
    workflow = SignalHarnessWorkflow(
        cwd=project_root,
        config_dir=project_root / "configs",
        output_dir=tmp_path / "outputs",
        state_dir=tmp_path / "state",
        mode=RunMode.DEMO,
        project_id="discovery-window-test",
        intelligence_pipeline=True,
    )
    event = SignalEvent(
        event_id="repo-1",
        source_type="github_repository",
        source_name="acme/new-tool",
        title="acme/new-tool",
        content="A new adjacent solution discovered today.",
        url="https://github.com/acme/new-tool",
        collected_at=now,
        published_at=now - timedelta(days=20),
        raw_payload={
            "discovery_origin": "discovered",
            "discovery_basis": '"api contract" developer tool',
        },
    )
    window = ResolvedScanWindow(
        mode="24h",
        lower=now - timedelta(days=1),
        upper=now + timedelta(seconds=1),
        first_use=False,
        checkpoint_eligible=False,
    )
    first = workflow._select_for_window(event, window)
    assert first is not None
    assert first.raw_payload["window_exception"] == "discovered_during_scan"
    workflow.ledger.persist_observations([event])
    assert workflow._select_for_window(event, window) is None


def test_single_change_brief_cannot_claim_market_trend() -> None:
    discovered = _change("c1", entity="acme/new-tool", source="acme/new-tool", discovered=True)
    output = SynthesisOutput(
        brief=[{"text": "这个方向正在成为新的技术趋势。", "supporting_change_ids": ["c1"]}],
        radar=[],
    )
    with pytest.raises(ValueError, match="Single Change brief"):
        validate_synthesis_semantics(output, {"c1": discovered})
