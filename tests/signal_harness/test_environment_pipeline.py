from __future__ import annotations

import asyncio
import json
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest

from signal_harness.intelligence.contracts import InsightBatch, SynthesisOutput
from signal_harness.intelligence.corpus import (
    assemble_changes,
    finalize_direction,
    source_revision_events,
)
from signal_harness.intelligence.engine import EnvironmentEngine
from signal_harness.intelligence.model_calls import BoundedModelCaller
from signal_harness.persistence import ChangeLedger
from signal_harness.persistence.intelligence import IntelligenceRepository
from signal_harness.providers.adapter import AgentCall
from signal_harness.providers.task_policy import TaskPolicy
from signal_harness.runtime.tracing import TraceRecorder
from signal_harness.signal.schemas import SignalEvent

NOW = datetime(2026, 9, 10, 8, tzinfo=timezone.utc)


class ScriptedIntelligenceProvider:
    name = "offline-contract-test"
    model = "scripted-not-a-real-model"

    def __init__(self, calls: list[AgentCall], fault: str = "") -> None:
        self.calls, self.fault = calls, fault

    async def close(self) -> None:
        pass

    async def complete(self, call: AgentCall) -> str:
        self.calls.append(call)
        payload = call.input_payload
        if call.output_schema == InsightBatch.__name__:
            results = [
                {
                    "change_id": item["change_id"],
                    "summary": "上游工具接口发生调整：" + item["title"],
                    "what_changed": "来源说明工具注册接口增加了显式版本。",
                    "project_relation": "direct" if "ignore" not in item["title"] else "none",
                    "relation_reason": "项目使用工具注册接口，尚未核实具体调用。",
                    "attention": "watch",
                    "topics": ["tool-registry"],
                    "evidence_ids": [item["evidence"][0]["evidence_id"]],
                    "uncertainty": "还没有检查项目运行行为。",
                }
                for item in payload["changes"]
            ]
            if self.fault == "missing":
                results = results[:-1]
            if self.fault == "foreign_evidence":
                results[0]["evidence_ids"] = ["evr-nonexistent"]
            if self.fault == "large_batch" and len(payload["changes"]) > 4:
                results = results[:-1]
            return json.dumps({"results": results}, ensure_ascii=False)
        if call.output_schema == SynthesisOutput.__name__:
            ids = [item["change_id"] for item in payload["corpus"]]
            refs = ids[-3:] if len(ids) >= 3 else ids
            if self.fault == "foreign_direction":
                refs = [*refs, "chg-not-in-corpus"]
            previous = payload.get("previous_report")
            prior = previous["directions"][0] if previous and previous.get("directions") else None
            directions = (
                [
                    {
                        "topic_key": "tool-registry",
                        "previous_direction_id": prior["direction_id"] if prior else None,
                        "title": "工具注册接口开始明确版本边界",
                        "explanation": "分散在不同批次的变更都涉及工具注册版本。",
                        "supporting_change_ids": refs,
                        "contradicting_change_ids": [],
                        "project_connection": "值得检查现有工具注册是否依赖旧格式。",
                        "watch_next": ["关注后续稳定版规范"],
                        "uncertainty": "样本只来自已配置来源。",
                    }
                ]
                if len(refs) >= 2
                else []
            )
            return json.dumps(
                {
                    "brief": [
                        {"text": "本期多条更新涉及工具注册版本。", "supporting_change_ids": refs}
                    ],
                    "directions": directions,
                    "featured_change_ids": ids[:5],
                },
                ensure_ascii=False,
            )
        return json.dumps(
            {
                "summary": "已基于来源证据分析。",
                "impact": "目前只能确认潜在接口关系。",
                "evidence_ids": [payload["change"]["evidence"][0]["evidence_id"]],
                "usage_reference_ids": [],
                "verification_steps": ["检查兼容性测试"],
                "uncertainty": "未执行代码。",
            },
            ensure_ascii=False,
        )


def setup_engine(
    tmp_path: Path, project_root: Path, count: int = 300, fault: str = ""
) -> tuple[Any, ...]:
    ledger = ChangeLedger(tmp_path / "ledger.sqlite3")
    profile = ledger.ensure_profile_revision(
        project_id="demo", auto_profile={"project_name": "测试项目", "dependencies": ["mcp"]}
    )
    ledger.begin_scan(
        scan_id="scan-test",
        project_id="demo",
        collected_count=count,
        deduped_count=count,
        profile_revision_id=profile["profile_revision_id"],
    )
    events = [
        SignalEvent(
            event_id=f"e-{i:04}",
            source_type="web_change",
            source_name=f"source-{i % 7}",
            title=f"变更 {i:04}",
            content=f"工具注册更新 {i}",
            url=f"https://example.org/news/{i}",
            collected_at=NOW,
            published_at=NOW,
            raw_payload={"source_authority": "official"},
        )
        for i in range(count)
    ]
    mapping = ledger.persist_observations(events)
    digests = assemble_changes(events, mapping)
    calls: list[AgentCall] = []
    policy = TaskPolicy.load(project_root / "configs")
    caller = BoundedModelCaller(
        policy,
        TraceRecorder(),
        lambda name, role: ScriptedIntelligenceProvider(calls, fault),
        {role: ["offline"] for role in ("shallow", "synthesis", "deep_dive")},
    )
    repo = IntelligenceRepository(ledger.path)
    engine = EnvironmentEngine(repo, caller)
    kwargs = {
        "scan_id": "scan-test",
        "project_id": "demo",
        "profile_revision_id": profile["profile_revision_id"],
        "profile": profile["effective_profile"],
        "digests": digests,
        "window": {"from": "2026-09-08T00:00:00+00:00", "to": "2026-09-11T00:00:00+00:00"},
        "observed_count": count,
        "sources": [],
        "coverage_status": "complete",
    }
    return engine, kwargs, calls, repo, ledger, events, digests


def test_300_changes_all_reach_global_model_without_topk(
    tmp_path: Path, project_root: Path
) -> None:
    engine, kwargs, calls, repo, *_ = setup_engine(tmp_path, project_root)
    progress = []
    engine.listener = progress.append
    report = asyncio.run(engine.run(**kwargs))
    assert report.status == "complete"
    assert report.counts["interpreted"] == report.counts["changes"] == 300
    assert report.counts["automatic_deep_dives"] == 0
    expected_batches = (
        300 + engine.caller.policy.batch_size - 1
    ) // engine.caller.policy.batch_size
    assert len(calls) == expected_batches + 1  # bounded batches + ONE synthesis, never per-item.
    assert len(calls[-1].input_payload["corpus"]) == 300
    assert len({c["change_id"] for c in calls[-1].input_payload["corpus"]}) == 300
    assert set(report.directions[0].supporting_change_ids).isdisjoint(
        {c.change_id for c in report.featured}
    )
    assert len(report.featured) == 5
    assert report.directions[0].state == "new"
    assert progress[-1].stage == "complete"
    with repo.connect() as db:
        assert db.execute("SELECT count(*) FROM change_revisions").fetchone()[0] == 300
        assert db.execute("SELECT count(*) FROM deep_dive_jobs").fetchone()[0] == 0
    assert repo.changes("scan-test", offset=295)["count"] == 300
    assert len(repo.changes("scan-test", offset=295)["items"]) == 5
    assert not repo.changes("scan-test", offset=295)["has_more"]
    assert repo.changes("scan-test", query="变更 0299")["count"] == 1
    assert asyncio.run(engine.run(**kwargs)).report_id == report.report_id
    assert len(calls) == expected_batches + 1  # committed report reload incurs no calls.


@pytest.mark.parametrize("fault", ["missing", "foreign_evidence"])
def test_failed_shallow_batch_is_visible_not_faked(
    tmp_path: Path, project_root: Path, fault: str
) -> None:
    engine, kwargs, calls, repo, *_ = setup_engine(tmp_path, project_root, 21, fault)
    report = asyncio.run(engine.run(**kwargs))
    assert report.status == "degraded"
    assert report.counts["unavailable"] == 21
    assert report.counts["interpreted"] == 0
    assert report.featured == []
    assert len(calls[-1].input_payload["corpus"]) == 21
    assert repo.changes("scan-test", view="unavailable")["count"] == 21


def test_unknown_direction_citations_rejected(tmp_path: Path, project_root: Path) -> None:
    engine, kwargs, *_ = setup_engine(tmp_path, project_root, 8, "foreign_direction")
    report = asyncio.run(engine.run(**kwargs))
    assert report.status == "degraded"
    assert report.directions == []
    assert report.brief == []
    assert report.counts["interpreted"] == 8


def test_over_budget_never_truncates_to_featured(tmp_path: Path, project_root: Path) -> None:
    engine, kwargs, calls, *_ = setup_engine(tmp_path, project_root, 50)
    engine.caller.policy = replace(engine.caller.policy, global_input_bytes=500)
    report = asyncio.run(engine.run(**kwargs))
    assert report.status == "degraded"
    assert report.counts["interpreted"] == 50
    assert all(c.output_schema == "InsightBatch" for c in calls)


def test_revision_changes_preserve_old_snapshot(tmp_path: Path, project_root: Path) -> None:
    engine, kwargs, _, repo, ledger, events, digests = setup_engine(tmp_path, project_root, 1)
    asyncio.run(engine.run(**kwargs))
    updated = events[0].model_copy(update={"content": "修订后的不同事实"})
    later = assemble_changes([updated], ledger.persist_observations([updated]))
    assert later[0].change_id == digests[0].change_id
    assert later[0].revision_id != digests[0].revision_id
    assert repo.change("scan-test", digests[0].change_id)["revision_id"] == digests[0].revision_id
    with pytest.raises(ValueError, match="overwritten"):
        repo.freeze("scan-test", later)


def test_same_title_different_content_not_lost_across_scans(
    tmp_path: Path, project_root: Path
) -> None:
    *_, events, _ = setup_engine(tmp_path, project_root, 1)
    changed = events[0].model_copy(update={"content": "New text"})
    assert source_revision_events([events[0], changed])[0].content == "New text"


def test_direction_never_invents_growth_without_comparable_history(
    tmp_path: Path, project_root: Path
) -> None:
    engine, kwargs, calls, *_ = setup_engine(tmp_path, project_root, 5)
    report = asyncio.run(engine.run(**kwargs))
    candidate = SynthesisOutput.model_validate_json(
        asyncio.run(ScriptedIntelligenceProvider([]).complete(calls[-1]))
    ).directions[0]
    by_id = {c.change_id: c for c in report.featured}
    direction = finalize_direction(
        candidate,
        changes=by_id,
        project_id="demo",
        scan_id="scan-2",
        previous=report.model_dump(),
        window=kwargs["window"],
        coverage="partial",
    )
    assert direction.state == "uncertain"


def test_product_language_guard_rejects_valid_but_english_json():
    from signal_harness.intelligence.contracts import GroundedClaim, SynthesisOutput
    from signal_harness.intelligence.model_calls import validate_product_language

    output = SynthesisOutput(
        brief=[
            GroundedClaim(
                text="The environment is changing across providers.", supporting_change_ids=["c1"]
            )
        ]
    )
    with pytest.raises(ValueError, match="simplified_chinese"):
        validate_product_language(output)


def test_cross_source_assembly_is_one_change_with_two_sources(tmp_path: Path):
    ledger = ChangeLedger(tmp_path / "ledger.sqlite3")
    first = SignalEvent(
        event_id="pypi-mcp-v2",
        source_type="package_registry",
        source_name="mcp",
        title="mcp 2.2.0 released",
        current_version="2.2.0",
        content="release metadata",
        url="https://pypi.org/project/mcp/2.2.0",
        collected_at=NOW,
        raw_payload={"package_name": "mcp", "registry": "pypi", "source_authority": "official"},
    )
    second = SignalEvent(
        event_id="github-mcp-v2",
        source_type="github_release",
        source_name="modelcontextprotocol/python-sdk",
        title="mcp v2.2.0 release notes",
        current_version="v2.2.0",
        content="release details",
        url="https://github.com/modelcontextprotocol/python-sdk/releases/tag/v2.2.0",
        collected_at=NOW,
        raw_payload={
            "package_name": "mcp",
            "package_registry": "pypi",
            "source_authority": "maintainer",
        },
    )
    digests = assemble_changes([first, second], ledger.persist_observations([first, second]))
    assert len(digests) == 1
    assert len(digests[0].evidence) == 2


def test_new_project_revision_invalidates_insight_cache(tmp_path: Path, project_root: Path):
    engine, kwargs, calls, repo, ledger, _, _ = setup_engine(tmp_path, project_root, 4)
    asyncio.run(engine.run(**kwargs))
    old_calls = len(calls)
    profile = ledger.ensure_profile_revision(
        project_id="demo", auto_profile={"project_name": "测试项目", "dependencies": ["react"]}
    )
    ledger.begin_scan(
        scan_id="scan-new-profile",
        project_id="demo",
        collected_count=4,
        deduped_count=4,
        profile_revision_id=profile["profile_revision_id"],
    )
    new = {
        **kwargs,
        "scan_id": "scan-new-profile",
        "profile_revision_id": profile["profile_revision_id"],
        "profile": profile["effective_profile"],
    }
    result = asyncio.run(engine.run(**new))
    assert result.counts["cache_hits"] == 0
    assert len(calls) == old_calls + 2


def test_production_runtime_and_providers_do_not_import_eval(project_root: Path):
    import ast

    root = project_root / "src" / "signal_harness"
    forbidden = {"capability_eval", "harness_eval", "generic_monitor_eval", "evals"}
    violations = []
    for folder in ("providers", "runtime", "intelligence", "persistence"):
        for path in (root / folder).rglob("*.py"):
            for node in ast.walk(ast.parse(path.read_text())):
                if isinstance(node, ast.ImportFrom):
                    modules = [node.module or ""]
                elif isinstance(node, ast.Import):
                    modules = [alias.name for alias in node.names]
                else:
                    continue
                if any(set(module.split(".")) & forbidden for module in modules):
                    violations.append(f"{path.relative_to(root)}:{node.lineno}")
    assert violations == []


def _product_change(
    change_id: str,
    *,
    entity: str,
    date: str,
    source: str,
    corpus_role: str = "external_environment",
):
    from signal_harness.intelligence.contracts import Evidence, ProductChange

    return ProductChange(
        change_id=change_id,
        revision_id="rev-" + change_id,
        title=f"{entity} 更新",
        entity=entity,
        kind="github_release",
        published_at=date,
        corpus_role=corpus_role,
        summary=f"{entity} 发布了新的上游变化。",
        what_changed=f"{entity} 的公开资料出现了新的版本或接口变化。",
        project_relation="context",
        relation_reason="项目使用相关生态，需要关注兼容性。",
        attention="normal",
        topics=["工具协议"],
        uncertainty="尚未深入核实具体代码影响。",
        interpretation_status="ready",
        relevant=corpus_role == "external_environment",
        evidence=[
            Evidence(
                evidence_id="ev-" + change_id,
                event_revision_id=int(change_id.removeprefix("c")),
                source_name=source,
                source_type="github_release",
                url=f"https://example.com/{change_id}",
                authority="official",
                excerpt="官方资料中的变化说明。",
                project_owned=corpus_role == "project_activity",
            )
        ],
    )


def test_project_owned_change_is_context_not_environment_direction(tmp_path: Path):
    from signal_harness.intelligence.contracts import DirectionCandidate, SynthesisOutput
    from signal_harness.intelligence.quality import validate_synthesis_semantics

    external = _product_change(
        "c1", entity="OpenAI", date="2026-09-07T00:00:00+00:00", source="openai"
    )
    own = _product_change(
        "c2",
        entity="SignalHarness",
        date="2026-09-08T00:00:00+00:00",
        source="signalharness",
        corpus_role="project_activity",
    )
    output = SynthesisOutput(
        brief=[{"text": "外部接口出现兼容性变化。", "supporting_change_ids": ["c1"]}],
        directions=[
            DirectionCandidate(
                topic_key="tool-runtime",
                title="工具运行接口出现共同调整",
                explanation="不同主体都在调整工具运行接口。",
                supporting_change_ids=["c1", "c2"],
            )
        ],
        featured_change_ids=["c1"],
    )
    with pytest.raises(ValueError, match="external-environment"):
        validate_synthesis_semantics(output, {"c1": external, "c2": own})


def test_temporal_same_day_claim_must_match_cited_dates():
    from signal_harness.intelligence.contracts import SynthesisOutput
    from signal_harness.intelligence.quality import validate_synthesis_semantics

    changes = {
        "c1": _product_change("c1", entity="MCP", date="2026-09-07T00:00:00+00:00", source="pypi"),
        "c2": _product_change(
            "c2", entity="OpenAI", date="2026-09-08T00:00:00+00:00", source="github"
        ),
    }
    output = SynthesisOutput(
        brief=[{"text": "两个变化在同日发布。", "supporting_change_ids": ["c1", "c2"]}],
        featured_change_ids=["c1"],
    )
    with pytest.raises(ValueError, match="same-day"):
        validate_synthesis_semantics(output, changes)


def test_direction_overlap_and_velocity_are_rejected():
    from signal_harness.intelligence.contracts import DirectionCandidate, SynthesisOutput
    from signal_harness.intelligence.quality import validate_synthesis_semantics

    changes = {
        "c1": _product_change(
            "c1", entity="OpenAI", date="2026-09-07T00:00:00+00:00", source="openai"
        ),
        "c2": _product_change(
            "c2", entity="Anthropic", date="2026-09-08T00:00:00+00:00", source="anthropic"
        ),
        "c3": _product_change("c3", entity="MCP", date="2026-09-09T00:00:00+00:00", source="mcp"),
    }
    velocity = SynthesisOutput(
        brief=[{"text": "工具接口出现多项变化。", "supporting_change_ids": ["c1", "c2"]}],
        directions=[
            DirectionCandidate(
                topic_key="tools",
                title="工具接口调整正在加速",
                explanation="多个主体都在调整工具接口。",
                supporting_change_ids=["c1", "c2"],
            )
        ],
        featured_change_ids=["c1"],
    )
    with pytest.raises(ValueError, match="trend state"):
        validate_synthesis_semantics(velocity, changes)

    overlap = SynthesisOutput(
        brief=[{"text": "工具接口出现多项变化。", "supporting_change_ids": ["c1", "c2"]}],
        directions=[
            DirectionCandidate(
                topic_key="tools-a",
                title="工具接口兼容性出现共同调整",
                explanation="多个工具接口出现兼容性和流式解析调整。",
                supporting_change_ids=["c1", "c2"],
            ),
            DirectionCandidate(
                topic_key="tools-b",
                title="工具接口兼容与解析出现共同变化",
                explanation="多个工具接口都涉及兼容性以及流式解析变化。",
                supporting_change_ids=["c1", "c3"],
            ),
        ],
        featured_change_ids=["c1"],
    )
    with pytest.raises(ValueError, match="overlapping"):
        validate_synthesis_semantics(overlap, changes)


def test_product_copy_rejects_internal_contract_vocabulary():
    from signal_harness.intelligence.contracts import SynthesisOutput
    from signal_harness.intelligence.model_calls import validate_product_language

    output = SynthesisOutput(
        brief=[
            {
                "text": "项目的 critical_modules 中包含 MCP，需要关注上游变化。",
                "supporting_change_ids": ["c1"],
            }
        ]
    )
    with pytest.raises(ValueError, match="internal_contract"):
        validate_product_language(output)


def test_product_copy_rejects_change_id_even_when_attached_to_chinese_text():
    from signal_harness.intelligence.contracts import SynthesisOutput
    from signal_harness.intelligence.model_calls import validate_product_language

    output = SynthesisOutput(
        brief=[
            {
                "text": "这个变化与chg-deadbeef123456属同类问题。",
                "supporting_change_ids": ["c1"],
            }
        ]
    )
    with pytest.raises(ValueError, match="internal_contract"):
        validate_product_language(output)


def test_project_activity_is_shallowly_interpreted_but_not_featured(
    tmp_path: Path, project_root: Path
):
    engine, kwargs, calls, repo, *_ = setup_engine(tmp_path, project_root, 3)
    digests = list(kwargs["digests"])
    digests[0] = digests[0].model_copy(update={"corpus_role": "project_activity"})
    kwargs["digests"] = digests
    report = asyncio.run(engine.run(**kwargs))
    assert report.counts["changes"] == 3
    assert report.counts["external_changes"] == 2
    assert report.counts["project_activity"] == 1
    assert report.counts["interpreted"] == 3
    assert len(calls[-1].input_payload["corpus"]) == 2
    assert len(calls[-1].input_payload["project_activity"]) == 1
    assert all(item.corpus_role == "external_environment" for item in report.featured)
    assert repo.changes("scan-test", view="all")["count"] == 2
    assert repo.changes("scan-test", view="activity")["count"] == 1
    activity = repo.changes("scan-test", view="activity")["items"][0]
    assert activity["interpretation_status"] == "ready"
    assert activity["relevant"] is False


def test_project_owned_event_is_assembled_as_project_activity(tmp_path: Path):
    ledger = ChangeLedger(tmp_path / "ledger.sqlite3")
    event = SignalEvent(
        event_id="own-commit",
        source_type="github_commit",
        source_name="owner/project",
        title="Refactor runtime",
        content="internal project change",
        url="https://github.com/owner/project/commit/abc",
        collected_at=NOW,
        published_at=NOW,
        raw_payload={"project_owned": True, "source_authority": "official"},
    )
    digest = assemble_changes([event], ledger.persist_observations([event]))[0]
    assert digest.corpus_role == "project_activity"
    assert digest.evidence[0].project_owned is True


def test_schema_v8_migrates_existing_scan_intelligence_role_column(tmp_path: Path):
    import sqlite3

    path = tmp_path / "old.sqlite3"
    with sqlite3.connect(path) as db:
        db.execute(
            "CREATE TABLE scan_intelligence("
            "scan_id TEXT NOT NULL, change_id TEXT NOT NULL, revision_id TEXT NOT NULL, "
            "insight_json TEXT, relevant INTEGER NOT NULL DEFAULT 0, featured INTEGER NOT NULL DEFAULT 0, "
            "attention TEXT NOT NULL DEFAULT 'normal', interpreted INTEGER NOT NULL DEFAULT 0, "
            "published_at TEXT, search_text TEXT NOT NULL DEFAULT '', PRIMARY KEY(scan_id,change_id))"
        )
    ChangeLedger(path)
    with sqlite3.connect(path) as db:
        columns = {row[1] for row in db.execute("PRAGMA table_info(scan_intelligence)")}
        version = db.execute("SELECT value FROM schema_meta WHERE key='schema_version'").fetchone()[
            0
        ]
    assert "corpus_role" in columns
    assert version == "8"


def test_large_shallow_batch_splits_without_losing_full_corpus(tmp_path: Path, project_root: Path):
    engine, kwargs, calls, _, *_ = setup_engine(tmp_path, project_root, 12, "large_batch")
    report = asyncio.run(engine.run(**kwargs))
    assert report.status == "complete"
    assert report.counts["interpreted"] == 12
    shallow_calls = [call for call in calls if call.output_schema == InsightBatch.__name__]
    assert any(len(call.input_payload["changes"]) == 12 for call in shallow_calls)
    assert any(len(call.input_payload["changes"]) <= 3 for call in shallow_calls)
    assert len(calls[-1].input_payload["corpus"]) == 12


def test_direction_evidence_posture_and_source_authority_are_deterministic(tmp_path: Path):
    from signal_harness.intelligence.contracts import DirectionCandidate, ShallowInsight
    from signal_harness.intelligence.corpus import finalize_direction, project_change

    ledger = ChangeLedger(tmp_path / "ledger.sqlite3")
    issue_a = SignalEvent(
        event_id="issue-a",
        source_type="github_issue",
        source_name="org/repo-a",
        title="Streaming parse problem",
        content="Reporter describes a streaming failure.",
        url="https://github.com/org/repo-a/issues/1",
        collected_at=NOW,
        published_at=NOW,
        raw_payload={"author_association": "NONE"},
    )
    issue_b = SignalEvent(
        event_id="issue-b",
        source_type="github_issue",
        source_name="org/repo-b",
        title="Related stream problem",
        content="Another project reports a streaming failure.",
        url="https://github.com/org/repo-b/issues/2",
        collected_at=NOW,
        published_at=NOW,
        raw_payload={"author_association": "MEMBER"},
    )
    events = [issue_a, issue_b]
    digests = assemble_changes(events, ledger.persist_observations(events))
    assert {d.evidence[0].authority for d in digests} == {"community", "maintainer"}
    products = {}
    for digest in digests:
        insight = ShallowInsight(
            change_id=digest.change_id,
            summary="两个项目都报告了流式解析问题。",
            what_changed="公开 Issue 描述了流式解析失败。",
            project_relation="context",
            relation_reason="项目包含流式处理，需要关注上游问题信号。",
            attention="watch",
            topics=["流式解析"],
            evidence_ids=[digest.evidence[0].evidence_id],
        )
        products[digest.change_id] = project_change(digest, insight, {})
    candidate = DirectionCandidate(
        topic_key="streaming-reports",
        title="多个项目报告流式解析问题",
        explanation="两个独立项目的 Issue 都描述了流式解析异常。",
        supporting_change_ids=list(products),
    )
    direction = finalize_direction(
        candidate,
        changes=products,
        project_id="demo",
        scan_id="scan",
        previous=None,
        window={"from": None, "to": NOW.isoformat()},
        coverage="complete",
        sources=[],
    )
    assert direction.evidence_posture == "reported_issue"
    assert direction.independent_source_count == 2
    assert direction.authoritative_source_count == 1


def test_official_rss_authority_uses_deterministic_source_quality(tmp_path: Path):
    ledger = ChangeLedger(tmp_path / "ledger.sqlite3")
    event = SignalEvent(
        event_id="official-news",
        source_type="rss",
        source_name="Official News",
        title="New release announced",
        content="The official feed announces a release.",
        url="https://example.com/release",
        collected_at=NOW,
        published_at=NOW,
        raw_payload={"official": True},
    )
    digest = assemble_changes([event], ledger.persist_observations([event]))[0]
    assert digest.evidence[0].authority == "official"
