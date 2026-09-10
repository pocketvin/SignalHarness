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
            ids = [item.get("change_id", item.get("id")) for item in payload["corpus"]]
            refs = []
            seen_entities = set()
            for item in reversed(payload["corpus"]):
                entity = str(item.get("entity") or item.get("e") or "").casefold()
                if entity in seen_entities:
                    continue
                refs.append(item.get("change_id", item.get("id")))
                seen_entities.add(entity)
                if len(refs) == min(3, len(ids)):
                    break
            if len(refs) < min(2, len(ids)):
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
    assert len({c.get("change_id", c.get("id")) for c in calls[-1].input_payload["corpus"]}) == 300
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


def test_1000_structured_package_releases_skip_weak_model_and_keep_full_global_corpus(
    tmp_path: Path, project_root: Path
) -> None:
    ledger = ChangeLedger(tmp_path / "cost-aware.sqlite3")
    dependencies = [f"pkg-{index:04d}" for index in range(1000)]
    profile = ledger.ensure_profile_revision(
        project_id="cost-aware",
        auto_profile={"project_name": "成本测试项目", "dependencies": dependencies},
    )
    ledger.begin_scan(
        scan_id="scan-1000",
        project_id="cost-aware",
        collected_count=1000,
        deduped_count=1000,
        profile_revision_id=profile["profile_revision_id"],
    )
    events = [
        SignalEvent(
            event_id=f"release-{index:04d}",
            source_type="package_registry",
            source_name=package,
            title=f"{package} 1.0.{index}",
            content=f"PyPI release 1.0.{index} for {package}",
            url=f"https://pypi.org/project/{package}/1.0.{index}/",
            collected_at=NOW,
            published_at=NOW,
            current_version=f"1.0.{index}",
            raw_payload={
                "registry": "pypi",
                "package_name": package,
                "official": True,
                "source_authority": "official",
            },
        )
        for index, package in enumerate(dependencies)
    ]
    digests = assemble_changes(events, ledger.persist_observations(events))
    calls: list[AgentCall] = []
    policy = TaskPolicy.load(project_root / "configs")
    caller = BoundedModelCaller(
        policy,
        TraceRecorder(),
        lambda name, role: ScriptedIntelligenceProvider(calls),
        {role: ["offline"] for role in ("shallow", "synthesis", "deep_dive")},
    )
    repo = IntelligenceRepository(ledger.path)
    engine = EnvironmentEngine(repo, caller)
    report = asyncio.run(
        engine.run(
            scan_id="scan-1000",
            project_id="cost-aware",
            profile_revision_id=profile["profile_revision_id"],
            profile=profile["effective_profile"],
            digests=digests,
            window={"from": "2026-09-10T00:00:00+00:00", "to": "2026-09-11T00:00:00+00:00"},
            observed_count=1000,
            sources=[],
            coverage_status="complete",
        )
    )
    assert report.status == "complete"
    assert report.counts["interpreted"] == report.counts["changes"] == 1000
    assert report.counts["automatic_deep_dives"] == 0
    assert all(call.agent_name != "ChangeInterpreter" for call in calls)
    synthesis_calls = [call for call in calls if call.agent_name == "EnvironmentSynthesizer"]
    assert len(synthesis_calls) == 1
    assert len(synthesis_calls[0].input_payload["corpus"]) == 1000
    assert (
        len(
            {
                item.get("change_id", item.get("id"))
                for item in synthesis_calls[0].input_payload["corpus"]
            }
        )
        == 1000
    )
    with repo.connect() as db:
        audit = json.loads(
            db.execute(
                "SELECT audit_json FROM environment_reports WHERE scan_id='scan-1000'"
            ).fetchone()[0]
        )
    routing = audit["insight_routing"]
    assert routing["deterministic"] == 1000
    assert routing["semantic"] == 0
    assert routing["planned_semantic_batches"] == 0
    assert routing["tiny_fast_path_activated"] is False
    assert audit["synthesis_input_bytes"] < policy.global_input_bytes


def test_balanced_batch_planner_spreads_entities_before_repeating(tmp_path: Path) -> None:
    from signal_harness.intelligence.batch_planner import BalancedBatchPlanner
    from signal_harness.intelligence.fact_capsule import build_fact_capsule
    from signal_harness.intelligence.semantic_router import SemanticWorkItem

    events = []
    for entity in ("alpha/repo", "beta/repo", "gamma/repo"):
        for index in range(3):
            events.append(
                SignalEvent(
                    event_id=f"{entity}-{index}",
                    source_type="github_issue",
                    source_name=entity,
                    title=f"Issue {index}",
                    content="A semantic issue that needs interpretation.",
                    url=f"https://example.com/{entity}/{index}",
                    collected_at=NOW,
                    published_at=NOW,
                    raw_payload={"author_association": "NONE"},
                )
            )
    ledger = ChangeLedger(tmp_path / "planner.sqlite3")
    digests = assemble_changes(events, ledger.persist_observations(events))
    work = [
        SemanticWorkItem(digest=digest, capsule=build_fact_capsule(digest, {}))
        for digest in digests
    ]
    batches = BalancedBatchPlanner(max_items=6, max_input_bytes=100000).plan(work)
    first_entities = [item.capsule.entity for item in batches[0]]
    assert first_entities[:3] == ["alpha/repo", "beta/repo", "gamma/repo"]
    assert first_entities[3:6] == ["alpha/repo", "beta/repo", "gamma/repo"]


class _ConcurrencyProbeProvider(ScriptedIntelligenceProvider):
    def __init__(self, calls: list[AgentCall], tracker: dict[str, int]) -> None:
        super().__init__(calls)
        self.tracker = tracker

    async def complete(self, call: AgentCall) -> str:
        if call.output_schema == InsightBatch.__name__:
            self.tracker["active"] += 1
            self.tracker["max_active"] = max(self.tracker["max_active"], self.tracker["active"])
            await asyncio.sleep(0.03)
            try:
                return await super().complete(call)
            finally:
                self.tracker["active"] -= 1
        return await super().complete(call)


def test_semantic_batches_use_bounded_parallelism(tmp_path: Path, project_root: Path) -> None:
    engine, kwargs, calls, _, *_ = setup_engine(tmp_path, project_root, 36)
    tracker = {"active": 0, "max_active": 0}
    engine.caller.policy = replace(
        engine.caller.policy,
        batch_size=4,
        batch_input_bytes=100000,
        shallow_concurrency=3,
    )
    engine.caller.factory = lambda name, role: _ConcurrencyProbeProvider(calls, tracker)
    report = asyncio.run(engine.run(**kwargs))
    assert report.status == "complete"
    assert tracker["max_active"] == 3
    assert report.counts["interpreted"] == 36
    with engine.repository.connect() as db:
        audit = json.loads(
            db.execute(
                "SELECT audit_json FROM environment_reports WHERE scan_id='scan-test'"
            ).fetchone()[0]
        )
    assert audit["insight_routing"]["planned_semantic_batches"] == 9
    assert audit["insight_routing"]["shallow_concurrency"] == 3


def test_tiny_fast_path_is_only_a_decision_point_until_eval(
    tmp_path: Path, project_root: Path
) -> None:
    engine, kwargs, calls, _, *_ = setup_engine(tmp_path, project_root, 3)
    report = asyncio.run(engine.run(**kwargs))
    assert report.status == "complete"
    # Small corpus qualifies for a future strong-only fast path, but this release deliberately
    # keeps the normal weak + strong path until a bounded real-model comparison is approved.
    assert any(call.agent_name == "ChangeInterpreter" for call in calls)
    with engine.repository.connect() as db:
        audit = json.loads(
            db.execute(
                "SELECT audit_json FROM environment_reports WHERE scan_id='scan-test'"
            ).fetchone()[0]
        )
    assert audit["insight_routing"]["tiny_fast_path_candidate"] is True
    assert audit["insight_routing"]["tiny_fast_path_activated"] is False


def test_direction_digest_keeps_every_change_without_repeating_long_evidence(
    tmp_path: Path,
) -> None:
    from signal_harness.intelligence.contracts import Evidence, ProductChange
    from signal_harness.intelligence.direction_digest import organize_direction_corpus

    changes = [
        ProductChange(
            change_id=f"c{index}",
            revision_id=f"r{index}",
            title="Long evidence change",
            entity=f"entity-{index % 10}",
            kind="github_issue",
            published_at=NOW.isoformat(),
            summary="上游报告了一个需要关注的兼容性变化。",
            what_changed="流式解析在特定控制事件上可能提前结束，需要继续确认。" * 8,
            project_relation="context",
            relation_reason="项目包含相关流式处理路径。" * 8,
            attention="normal",
            topics=["流式解析", "兼容性"],
            uncertainty="当前信息来自问题报告，尚未确认修复状态。",
            interpretation_status="ready",
            relevant=True,
            evidence=[
                Evidence(
                    evidence_id=f"e{index}",
                    event_revision_id=index + 1,
                    source_name=f"repo-{index % 10}",
                    source_type="github_issue",
                    url=f"https://example.com/{index}",
                    authority="community",
                    excerpt="X" * 1800,
                )
            ],
        )
        for index in range(1000)
    ]
    organized = organize_direction_corpus(changes)
    encoded = json.dumps(organized, ensure_ascii=False, separators=(",", ":")).encode()
    assert len(organized["items"]) == 1000
    assert len({item["id"] for item in organized["items"]}) == 1000
    assert all(
        "evidence" not in item and "relation_reason" not in item for item in organized["items"]
    )
    assert len(encoded) < 450000


def test_mixed_deterministic_semantic_then_cache_reuse_only_calls_new_semantics(
    tmp_path: Path, project_root: Path
) -> None:
    ledger = ChangeLedger(tmp_path / "mixed-routes.sqlite3")
    dependencies = [f"pkg-{index}" for index in range(12)]
    profile = ledger.ensure_profile_revision(
        project_id="mixed",
        auto_profile={"project_name": "混合路由项目", "dependencies": dependencies},
    )
    events: list[SignalEvent] = []
    for index, package in enumerate(dependencies):
        events.append(
            SignalEvent(
                event_id=f"release-{index}",
                source_type="package_registry",
                source_name=package,
                title=f"{package} 1.0.{index}",
                content=f"PyPI release 1.0.{index}",
                url=f"https://pypi.org/project/{package}/1.0.{index}/",
                collected_at=NOW,
                published_at=NOW,
                current_version=f"1.0.{index}",
                raw_payload={
                    "registry": "pypi",
                    "package_name": package,
                    "official": True,
                },
            )
        )
    for index in range(12):
        events.append(
            SignalEvent(
                event_id=f"issue-{index}",
                source_type="github_issue",
                source_name=f"repo-{index % 4}",
                title=f"Semantic issue {index}",
                content="A reported runtime behavior needs semantic interpretation.",
                url=f"https://example.com/issues/{index}",
                collected_at=NOW,
                published_at=NOW,
                raw_payload={"author_association": "NONE"},
            )
        )
    digests = assemble_changes(events, ledger.persist_observations(events))
    policy = TaskPolicy.load(project_root / "configs")
    repo = IntelligenceRepository(ledger.path)

    def run(scan_id: str, calls: list[AgentCall]) -> dict[str, Any]:
        ledger.begin_scan(
            scan_id=scan_id,
            project_id="mixed",
            collected_count=len(events),
            deduped_count=len(digests),
            profile_revision_id=profile["profile_revision_id"],
        )
        caller = BoundedModelCaller(
            policy,
            TraceRecorder(),
            lambda name, role: ScriptedIntelligenceProvider(calls),
            {role: ["offline"] for role in ("shallow", "synthesis", "deep_dive")},
        )
        engine = EnvironmentEngine(repo, caller)
        report = asyncio.run(
            engine.run(
                scan_id=scan_id,
                project_id="mixed",
                profile_revision_id=profile["profile_revision_id"],
                profile=profile["effective_profile"],
                digests=digests,
                window={
                    "from": "2026-09-10T00:00:00+00:00",
                    "to": "2026-09-11T00:00:00+00:00",
                },
                observed_count=len(events),
                sources=[],
                coverage_status="complete",
            )
        )
        assert report.status == "complete"
        with repo.connect() as db:
            return json.loads(
                db.execute(
                    "SELECT audit_json FROM environment_reports WHERE scan_id=?", (scan_id,)
                ).fetchone()[0]
            )

    first_calls: list[AgentCall] = []
    first = run("mixed-1", first_calls)
    assert first["insight_routing"]["deterministic"] == 12
    assert first["insight_routing"]["semantic"] == 12
    assert first["insight_routing"]["cache_hits"] == 0
    assert sum(call.agent_name == "ChangeInterpreter" for call in first_calls) == 1

    second_calls: list[AgentCall] = []
    second = run("mixed-2", second_calls)
    assert second["insight_routing"]["deterministic"] == 12
    assert second["insight_routing"]["semantic"] == 0
    assert second["insight_routing"]["cache_hits"] == 12
    assert all(call.agent_name != "ChangeInterpreter" for call in second_calls)
    assert sum(call.agent_name == "EnvironmentSynthesizer" for call in second_calls) == 1


def test_legacy_shallow_cache_is_validated_and_promoted_without_model_recompute(
    tmp_path: Path, project_root: Path
) -> None:
    from signal_harness.intelligence.contracts import (
        CHANGE_INSIGHT_VERSION,
        LEGACY_CHANGE_INSIGHT_VERSIONS,
    )
    from signal_harness.intelligence.corpus import identity

    ledger = ChangeLedger(tmp_path / "legacy-cache.sqlite3")
    profile = ledger.ensure_profile_revision(
        project_id="legacy-cache", auto_profile={"project_name": "缓存项目"}
    )
    event = SignalEvent(
        event_id="legacy-event",
        source_type="web_change",
        source_name="upstream",
        title="上游接口发生变化",
        content="公开说明中描述了接口行为变化。",
        url="https://example.com/change",
        collected_at=NOW,
        published_at=NOW,
        raw_payload={"official": True},
    )
    digest = assemble_changes([event], ledger.persist_observations([event]))[0]
    ledger.begin_scan(
        scan_id="legacy-cache-scan",
        project_id="legacy-cache",
        collected_count=1,
        deduped_count=1,
        profile_revision_id=profile["profile_revision_id"],
    )
    repo = IntelligenceRepository(ledger.path)
    policy = TaskPolicy.load(project_root / "configs")
    fingerprint = policy.fingerprint("shallow")
    old_version = LEGACY_CHANGE_INSIGHT_VERSIONS[0]
    old_key = identity(
        "insight-",
        [digest.revision_id, profile["profile_revision_id"], old_version, fingerprint],
    )
    new_key = identity(
        "insight-",
        [digest.revision_id, profile["profile_revision_id"], CHANGE_INSIGHT_VERSION, fingerprint],
    )
    cached = {
        "insight": {
            "change_id": digest.change_id,
            "summary": "上游接口出现新的行为变化。",
            "what_changed": "公开说明描述了接口行为发生变化。",
            "project_relation": "context",
            "relation_reason": "该接口属于项目关注的外部环境。",
            "attention": "normal",
            "topics": ["接口变化"],
            "evidence_ids": [digest.evidence[0].evidence_id],
            "uncertainty": "尚未深入核实项目调用位置。",
        },
        "provenance": [{"version": old_version}],
    }
    repo.cache_insight(old_key, cached)
    calls: list[AgentCall] = []
    caller = BoundedModelCaller(
        policy,
        TraceRecorder(),
        lambda name, role: ScriptedIntelligenceProvider(calls),
        {role: ["offline"] for role in ("shallow", "synthesis", "deep_dive")},
    )
    engine = EnvironmentEngine(repo, caller)
    report = asyncio.run(
        engine.run(
            scan_id="legacy-cache-scan",
            project_id="legacy-cache",
            profile_revision_id=profile["profile_revision_id"],
            profile=profile["effective_profile"],
            digests=[digest],
            window={"from": "2026-09-10T00:00:00+00:00", "to": "2026-09-11T00:00:00+00:00"},
            observed_count=1,
            sources=[],
            coverage_status="complete",
        )
    )
    assert report.status == "complete"
    assert all(call.agent_name != "ChangeInterpreter" for call in calls)
    with repo.connect() as db:
        audit = json.loads(
            db.execute(
                "SELECT audit_json FROM environment_reports WHERE scan_id='legacy-cache-scan'"
            ).fetchone()[0]
        )
    assert audit["insight_routing"]["cache_hits"] == 1
    assert audit["insight_routing"]["legacy_cache_hits"] == 1
    assert repo.get_cached_insight(new_key) == cached


def test_yanked_package_release_stays_on_semantic_path(tmp_path: Path, project_root: Path) -> None:
    ledger = ChangeLedger(tmp_path / "yanked.sqlite3")
    profile = ledger.ensure_profile_revision(
        project_id="yanked",
        auto_profile={"project_name": "Yanked 项目", "dependencies": ["demo-pkg"]},
    )
    event = SignalEvent(
        event_id="yanked-release",
        source_type="package_registry",
        source_name="demo-pkg",
        title="demo-pkg 2.0.0",
        content="PyPI release 2.0.0 for demo-pkg; 2 distribution file(s), 2 yanked.",
        url="https://pypi.org/project/demo-pkg/2.0.0/",
        collected_at=NOW,
        published_at=NOW,
        current_version="2.0.0",
        raw_payload={"registry": "pypi", "package_name": "demo-pkg", "official": True},
    )
    digest = assemble_changes([event], ledger.persist_observations([event]))[0]
    from signal_harness.intelligence.fact_capsule import build_fact_capsule
    from signal_harness.intelligence.semantic_router import route_capsule

    capsule = build_fact_capsule(digest, profile["effective_profile"])
    assert capsule.deterministic_eligible is False
    assert route_capsule(capsule).route == "semantic"


def test_project_owned_english_commit_can_skip_weak_model_without_language_failure(
    tmp_path: Path, project_root: Path
) -> None:
    ledger = ChangeLedger(tmp_path / "own-english.sqlite3")
    profile = ledger.ensure_profile_revision(
        project_id="own", auto_profile={"project_name": "Own project"}
    )
    event = SignalEvent(
        event_id="abcdef1234567890",
        source_type="github_commit",
        source_name="owner/project",
        title="Refactor streaming runtime",
        content="Refactor streaming runtime",
        url="https://github.com/owner/project/commit/abcdef1234567890",
        collected_at=NOW,
        published_at=NOW,
        raw_payload={
            "project_owned": True,
            "sha": "abcdef1234567890",
            "repository": "owner/project",
            "repository_identity": "owner/project",
            "official": True,
        },
    )
    digest = assemble_changes([event], ledger.persist_observations([event]))[0]
    ledger.begin_scan(
        scan_id="own-scan",
        project_id="own",
        collected_count=1,
        deduped_count=1,
        profile_revision_id=profile["profile_revision_id"],
    )
    calls: list[AgentCall] = []
    caller = BoundedModelCaller(
        TaskPolicy.load(project_root / "configs"),
        TraceRecorder(),
        lambda name, role: ScriptedIntelligenceProvider(calls),
        {role: ["offline"] for role in ("shallow", "synthesis", "deep_dive")},
    )
    repo = IntelligenceRepository(ledger.path)
    report = asyncio.run(
        EnvironmentEngine(repo, caller).run(
            scan_id="own-scan",
            project_id="own",
            profile_revision_id=profile["profile_revision_id"],
            profile=profile["effective_profile"],
            digests=[digest],
            window={"from": "2026-09-10T00:00:00+00:00", "to": "2026-09-11T00:00:00+00:00"},
            observed_count=1,
            sources=[],
            coverage_status="complete",
        )
    )
    assert report.status == "complete"
    assert all(call.agent_name != "ChangeInterpreter" for call in calls)
    activity = repo.changes("own-scan", view="activity")["items"][0]
    assert activity["summary"].startswith("项目自身更新：")


def test_large_corpus_with_only_three_semantic_misses_is_not_tiny_fast_path(
    tmp_path: Path, project_root: Path
) -> None:
    from signal_harness.intelligence.fact_capsule import build_fact_capsule
    from signal_harness.intelligence.semantic_router import (
        SemanticWorkItem,
        tiny_fast_path_candidate,
    )

    ledger = ChangeLedger(tmp_path / "not-tiny.sqlite3")
    events = [
        SignalEvent(
            event_id=f"event-{index}",
            source_type="web_change",
            source_name=f"source-{index % 5}",
            title=f"Change {index}",
            content="Needs semantic interpretation.",
            url=f"https://example.com/{index}",
            collected_at=NOW,
            published_at=NOW,
            raw_payload={"official": True},
        )
        for index in range(500)
    ]
    digests = assemble_changes(events, ledger.persist_observations(events))
    items = [SemanticWorkItem(digest=d, capsule=build_fact_capsule(d, {})) for d in digests]
    policy = TaskPolicy.load(project_root / "configs")
    assert not tiny_fast_path_candidate(
        items,
        max_changes=policy.tiny_fast_path_max_changes,
        max_input_bytes=policy.tiny_fast_path_max_input_bytes,
    )


def test_1000_semantic_changes_remain_full_corpus_under_offline_budget(
    tmp_path: Path, project_root: Path
) -> None:
    engine, kwargs, calls, repo, *_ = setup_engine(tmp_path, project_root, 1000)
    report = asyncio.run(engine.run(**kwargs))
    assert report.status == "complete"
    assert report.counts["interpreted"] == 1000
    weak_calls = [call for call in calls if call.agent_name == "ChangeInterpreter"]
    strong_calls = [call for call in calls if call.agent_name == "EnvironmentSynthesizer"]
    assert len(weak_calls) == 84  # ceil(1000 / 12), all offline scripted calls.
    assert len(strong_calls) == 1
    assert len(strong_calls[0].input_payload["corpus"]) == 1000
    with repo.connect() as db:
        audit = json.loads(
            db.execute(
                "SELECT audit_json FROM environment_reports WHERE scan_id='scan-test'"
            ).fetchone()[0]
        )
    assert audit["insight_routing"]["semantic"] == 1000
    assert audit["insight_routing"]["deterministic"] == 0
    assert audit["synthesis_input_bytes"] < engine.caller.policy.global_input_bytes


def test_cross_source_package_release_with_rich_release_evidence_stays_semantic(tmp_path: Path):
    from signal_harness.intelligence.fact_capsule import build_fact_capsule
    from signal_harness.intelligence.semantic_router import route_capsule

    ledger = ChangeLedger(tmp_path / "cross-source-rich.sqlite3")
    package = SignalEvent(
        event_id="pypi-demo-2",
        source_type="package_registry",
        source_name="demo-pkg",
        title="demo-pkg 2.0.0",
        current_version="2.0.0",
        content="PyPI release 2.0.0 for demo-pkg; 2 distribution file(s), 0 yanked.",
        url="https://pypi.org/project/demo-pkg/2.0.0/",
        collected_at=NOW,
        published_at=NOW,
        raw_payload={"registry": "pypi", "package_name": "demo-pkg", "official": True},
    )
    release = SignalEvent(
        event_id="github-demo-2",
        source_type="github_release",
        source_name="org/demo",
        title="demo-pkg v2.0.0",
        current_version="v2.0.0",
        content="Release notes describe a breaking API behavior and migration steps.",
        url="https://github.com/org/demo/releases/tag/v2.0.0",
        collected_at=NOW,
        published_at=NOW,
        raw_payload={
            "package_registry": "pypi",
            "package_name": "demo-pkg",
            "official": True,
        },
    )
    digest = assemble_changes([package, release], ledger.persist_observations([package, release]))[
        0
    ]
    assert len(digest.evidence) == 2
    capsule = build_fact_capsule(digest, {"dependencies": ["demo-pkg"]})
    assert capsule.deterministic_relation == "direct"
    assert route_capsule(capsule).route == "semantic"


class _UsageAccountingProvider:
    name = "usage-probe"
    model = "usage-probe-model"

    def __init__(self) -> None:
        from signal_harness.providers.adapter import ProviderUsage

        self._usage = ProviderUsage(source="provider_reported_with_profile_pricing")

    async def close(self) -> None:
        pass

    def usage_snapshot(self):
        return self._usage

    async def complete(self, call: AgentCall) -> str:
        from signal_harness.providers.adapter import ProviderUsage

        self._usage = ProviderUsage(
            prompt_tokens=self._usage.prompt_tokens + 100,
            completion_tokens=self._usage.completion_tokens + 50,
            total_tokens=self._usage.total_tokens + 150,
            estimated_cost_usd=self._usage.estimated_cost_usd + 0.0015,
            source="provider_reported_with_profile_pricing",
        )
        item = call.input_payload["changes"][0]
        return json.dumps(
            {
                "results": [
                    {
                        "change_id": item["change_id"],
                        "summary": "上游接口出现一项变化。",
                        "what_changed": "公开资料描述了接口行为变化。",
                        "project_relation": "context",
                        "relation_reason": "这项变化属于项目关注的外部接口。",
                        "attention": "normal",
                        "topics": ["接口变化"],
                        "evidence_ids": [item["evidence"][0]["evidence_id"]],
                        "uncertainty": "尚未深入核实项目调用位置。",
                    }
                ]
            },
            ensure_ascii=False,
        )


def test_model_usage_counts_invalid_attempt_and_success(tmp_path: Path, project_root: Path) -> None:
    policy = TaskPolicy.load(project_root / "configs")
    provider = _UsageAccountingProvider()
    caller = BoundedModelCaller(
        policy,
        TraceRecorder(),
        lambda name, role: provider,
        {role: ["usage"] for role in ("shallow", "synthesis", "deep_dive")},
    )
    validation_calls = 0

    def reject_once(output: InsightBatch) -> None:
        nonlocal validation_calls
        validation_calls += 1
        if validation_calls == 1:
            raise ValueError("synthetic validation failure")

    payload = {
        "project": {},
        "changes": [
            {
                "change_id": "c1",
                "title": "change",
                "evidence": [{"evidence_id": "e1", "excerpt": "source"}],
            }
        ],
    }
    output = asyncio.run(caller.complete("shallow", payload, InsightBatch, reject_once))
    assert output.results[0].change_id == "c1"
    summary = caller.usage_summary()
    assert summary["total"]["attempts"] == 2
    assert summary["total"]["prompt_tokens"] == 200
    assert summary["total"]["completion_tokens"] == 100
    assert summary["total"]["total_tokens"] == 300
    assert summary["total"]["estimated_cost_usd"] == pytest.approx(0.003)
    assert summary["total"]["usage_unknown_attempts"] == 0
    assert summary["total"]["pricing_unknown_attempts"] == 0
    assert summary["total"]["estimated_cost_complete"] is True
    assert caller.audit[0]["status"] == "invalid_output"
    assert caller.audit[0]["total_tokens"] == 150
    assert caller.audit[1]["status"] == "success"
    assert caller.audit[1]["total_tokens"] == 150
    assert caller.trace.steps[0].total_tokens == 150
    assert caller.trace.steps[1].total_tokens == 150
