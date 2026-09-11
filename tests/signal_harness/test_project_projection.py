from __future__ import annotations

import pytest

from signal_harness.intelligence.contracts import ShallowModelRow
from signal_harness.intelligence.project_projection import (
    build_project_references,
    model_row_to_insight,
    project_reference_map,
    render_relation_reason,
    shallow_project_context,
)

PROFILE = {
    "project_name": "SignalHarness",
    "purpose": "Project Environment Intelligence",
    "dependencies": ["pydantic", "httpx", "mcp"],
    "protocols": ["Model Context Protocol"],
    "runtimes": ["Python", "ASGI"],
    "providers": [],
    "critical_modules": ["SSE", "provider adapter", "agent eval"],
    "monitored_ecosystem": ["LangGraph", "OpenAI-compatible provider APIs"],
    "tech_stack": ["Python", "Pydantic", "SignalHarness runtime"],
}


def test_compact_project_context_uses_prefix_position_refs_without_project_name() -> None:
    context = shallow_project_context(PROFILE)
    assert context["g"] == "Project Environment Intelligence"
    assert context["r"]["d"] == ["pydantic", "httpx", "mcp"]
    assert context["r"]["p"] == ["Model Context Protocol"]
    assert context["r"]["m"] == ["SSE", "provider adapter", "agent eval"]
    assert context["r"]["e"] == ["LangGraph", "OpenAI-compatible provider APIs"]
    assert "SignalHarness" not in str(context)
    # tech_stack is intentionally omitted because it duplicates concrete exposure/module refs.
    assert "t" not in context["r"]


def test_reference_ids_are_stable_and_match_wire_positions() -> None:
    refs = build_project_references(PROFILE)
    assert [(item.ref_id, item.label) for item in refs[:4]] == [
        ("d1", "pydantic"),
        ("d2", "httpx"),
        ("d3", "mcp"),
        ("p1", "Model Context Protocol"),
    ]
    assert project_reference_map(PROFILE)["m1"].label == "SSE"
    assert project_reference_map(PROFILE)["e1"].label == "LangGraph"


def test_model_row_renders_one_basis_plus_short_incremental_note() -> None:
    insight = model_row_to_insight(
        ShallowModelRow(
            id="c1",
            s="上游流式接口出现兼容性问题",
            f="问题报告描述了流式事件解析异常。",
            r="context",
            b="m1",
            n="若走该流式路径，需要关注兼容性",
            a="normal",
            t=["流式解析"],
            e=["e1"],
            u="尚未确认实际调用路径",
        ),
        references=project_reference_map(PROFILE),
    )
    assert insight.project_relation == "context"
    assert insight.relation_reason == "关注能力：SSE；若走该流式路径，需要关注兼容性。"
    assert "SignalHarness" not in insight.relation_reason
    assert len(insight.relation_reason) < 60


def test_invalid_or_forbidden_basis_is_rejected() -> None:
    refs = project_reference_map(PROFILE)
    with pytest.raises(ValueError, match="project_relation_basis_reference_invalid"):
        render_relation_reason(
            relation="direct", basis_id="m99", note="可能影响运行", references=refs
        )
    with pytest.raises(ValueError, match="project_relation_basis_must_be_empty"):
        render_relation_reason(relation="none", basis_id="d1", note="未发现关联", references=refs)
    assert (
        render_relation_reason(relation="none", basis_id="", note="", references=refs)
        == "未发现与当前项目的明确技术关联。"
    )
    assert (
        render_relation_reason(relation="unknown", basis_id="", note="", references=refs)
        == "现有项目画像不足以确认关联。"
    )


def test_ecosystem_cannot_be_promoted_to_direct_relation() -> None:
    from signal_harness.intelligence.contracts import ChangeDigest, Evidence
    from signal_harness.intelligence.project_projection import validate_model_projection

    digest = ChangeDigest(
        change_id="c-langgraph",
        revision_id="r-langgraph",
        title="LangGraph build issue",
        entity="langgraph",
        kind="github_issue",
        published_at="2026-09-11T00:00:00+00:00",
        evidence=[
            Evidence(
                evidence_id="e1",
                event_revision_id=1,
                source_name="langchain-ai/langgraph",
                source_type="github_issue",
                url="https://example.com/1",
                authority="community",
                excerpt="LangGraph build issue",
            )
        ],
    )
    row = ShallowModelRow(
        id=digest.change_id,
        s="LangGraph 构建出现问题",
        f="问题报告描述了构建失败状态传播异常。",
        r="direct",
        b="e1",
        n="可能影响相关工作流评估",
        a="normal",
        t=["build"],
        e=["e1"],
    )
    with pytest.raises(ValueError, match="project_relation_basis_direct_not_exact_exposure"):
        validate_model_projection(
            row,
            digest=digest,
            known_relation="unknown",
            references=project_reference_map(PROFILE),
        )


def test_concrete_context_basis_must_be_named_by_this_change() -> None:
    from signal_harness.intelligence.contracts import ChangeDigest, Evidence
    from signal_harness.intelligence.project_projection import validate_model_projection

    digest = ChangeDigest(
        change_id="c-openai",
        revision_id="r-openai",
        title="tool_call delta index issue",
        entity="openai",
        kind="github_issue",
        published_at="2026-09-11T00:00:00+00:00",
        evidence=[
            Evidence(
                evidence_id="e1",
                event_revision_id=1,
                source_name="openai/openai-python",
                source_type="github_issue",
                url="https://example.com/2",
                authority="community",
                excerpt="tool_call deltas with duplicate indexes are accumulated incorrectly",
            )
        ],
    )
    row = ShallowModelRow(
        id=digest.change_id,
        s="工具调用增量解析异常",
        f="重复索引导致工具调用结构累积错误。",
        r="context",
        b="d2",  # httpx is a project dependency but absent from this Change.
        n="可能影响工具调用解析",
        a="normal",
        t=["tool-calling"],
        e=["e1"],
    )
    with pytest.raises(ValueError, match="project_relation_basis_concrete_not_in_change"):
        validate_model_projection(
            row,
            digest=digest,
            known_relation="unknown",
            references=project_reference_map(PROFILE),
        )


def test_known_ecosystem_basis_overrides_model_overclaim_without_batch_failure() -> None:
    from signal_harness.intelligence.contracts import ChangeDigest, Evidence
    from signal_harness.intelligence.fact_capsule import build_fact_capsule

    profile = {
        **PROFILE,
        "monitored_ecosystem": ["OpenAI-compatible provider APIs"],
    }
    digest = ChangeDigest(
        change_id="c-openai-known",
        revision_id="r-openai-known",
        title="Streaming tool_call delta issue",
        entity="openai",
        kind="github_issue",
        published_at="2026-09-11T00:00:00+00:00",
        evidence=[
            Evidence(
                evidence_id="e1",
                event_revision_id=1,
                source_name="openai/openai-python",
                source_type="github_issue",
                url="https://example.com/openai",
                authority="community",
                excerpt="tool_call streaming delta parsing issue",
            )
        ],
    )
    capsule = build_fact_capsule(digest, profile)
    assert capsule.deterministic_relation == "context"
    assert capsule.deterministic_basis_kind == "ecosystem"
    assert capsule.deterministic_basis_label == "OpenAI-compatible provider APIs"

    # Even if a model overclaims direct and points at an unrelated concrete dependency,
    # deterministic project context wins per item; no whole-batch repair is needed.
    row = ShallowModelRow(
        id=digest.change_id,
        s="工具调用流式解析异常",
        f="问题报告描述了流式增量解析错误。",
        r="direct",
        b="d2",
        n="SignalHarness 的 OpenAI-compatible provider APIs 可能受影响",
        a="normal",
        t=["tool-calling"],
        e=["e1"],
    )
    insight = model_row_to_insight(
        row,
        references=project_reference_map(profile),
        digest=digest,
        known_relation=capsule.deterministic_relation,
        known_basis_kind=capsule.deterministic_basis_kind,
        known_basis_label=capsule.deterministic_basis_label,
    )
    assert insight.project_relation == "context"
    assert insight.relation_reason.startswith("关注生态：OpenAI-compatible provider APIs")
    assert "SignalHarness" not in insight.relation_reason


def test_compact_wire_prose_still_uses_product_language_guard() -> None:
    from signal_harness.intelligence.contracts import ShallowModelBatch
    from signal_harness.intelligence.model_calls import validate_product_language

    english = ShallowModelBatch(
        x=[
            ShallowModelRow(
                id="c1",
                s="This is English only",
                f="This change modifies a parser behavior",
                r="unknown",
                b="",
                n="",
                a="normal",
                t=["parser"],
                e=["e1"],
                u="",
            )
        ]
    )
    with pytest.raises(ValueError, match="product_language_must_be_simplified_chinese"):
        validate_product_language(english)

    leaked = ShallowModelBatch(
        x=[
            ShallowModelRow(
                id="c1",
                s="上游解析行为发生变化",
                f="该变化会影响 dependencies 字段的处理。",
                r="unknown",
                b="",
                n="",
                a="normal",
                t=["parser"],
                e=["e1"],
                u="",
            )
        ]
    )
    with pytest.raises(ValueError, match="product_copy_exposes_internal_contract_vocabulary"):
        validate_product_language(leaked)
