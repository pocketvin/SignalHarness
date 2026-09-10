"""Capability Golden evaluation with fair shared-evidence Agent baselines.

This module intentionally complements, rather than replaces, the existing regression
suite. Regression protects already-solved behavior. Capability evaluation is allowed
to expose weaknesses and therefore scores product quality across several dimensions
instead of optimizing one accuracy number.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass, replace
from pathlib import Path
from statistics import mean
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from signal_harness.agent_integration.invoker import AgentInvoker
from signal_harness.agent_integration.mode import RunMode
from signal_harness.agent_integration.prompts import PROMPT_VERSION, build_agent_call
from signal_harness.agent_integration.runner import AgentLoopLimits
from signal_harness.agent_integration.schemas import (
    ActionOutput,
    SharedEvidenceSingleItem,
    SharedEvidenceSingleOutput,
    ContextEvidenceItem,
    ContextEvidenceOutput,
    ImpactOutput,
    ProjectNarrativeOutput,
    SupervisorOutput,
    SupervisorRoute,
)
from signal_harness.agent_integration.scoring_bridge import (
    GUARDED_SCORING_VERSION,
    compute_guarded_score_decision,
    guarded_assessments,
)
from signal_harness.agent_team import ActionPlannerAgent, ImpactAnalystAgent, ProjectNarrativeAgent
from signal_harness.agents.classifier import ClassifierAgent
from signal_harness.providers.adapter import AgentProvider
from signal_harness.presentation import PRESENTATION_VERSION, sanitize_user_facing_actions
from signal_harness.runtime.tracing import TraceRecorder
from signal_harness.signal.schemas import (
    SignalAssessment,
    SignalDecision,
    SignalEvent,
    SourceQuality,
    TraceStep,
)
from signal_harness.utils.fs import atomic_write_text

CAPABILITY_EVAL_VERSION = "capability-eval-v2"
CAPABILITY_GRADER_VERSION = "capability-grader-v4"

TruthStatus = Literal["verified", "partially_verified", "unverified", "contradicted"]
RecommendationState = Literal[
    "plumbing_only",
    "invalid_provider_or_schema",
    "needs_repeated_real_trials",
    "baseline_matrix_ready_no_auto_promotion",
]

_INTERNAL_LEAKAGE_MARKERS = (
    "impact_score",
    "score_breakdown",
    "agent_score_breakdown",
    "schema_valid",
    "routing_reason",
    "fallback_used",
    "deterministic fallback",
    "approval required before",
    "human approval is required",
    " is not enabled",
    "permission_checks",
    "requires_approval",
    "project-wide",
)
_UNCERTAINTY_MARKERS = (
    "尚未证实",
    "尚不能证实",
    "证据不足",
    "未验证",
    "无法确认",
    "需要核实",
    "不确定",
    "不能据此",
    "尚未合并",
    "未合并",
    "仍处于开放讨论",
    "仍处于未合并",
    "尚未纳入",
    "未纳入正式",
    "仅处于",
    "未公开",
    "缺乏",
    "缺失",
    "无法判断",
    "尚无",
    "未发现直接",
    "仅显示使用",
    "仅调用",
    "未提供",
    "未落地",
    "评论文章提出",
    "工程评论提出",
    "观点类文章",
    "观点文章",
    "partially verified",
    "unverified",
    "not verified",
    "insufficient evidence",
    "still open",
    "not merged",
    "not released",
    "under discussion",
)
_UNCERTAINTY_CRITERION_NAMES = {
    "not-affected",
    "proposal",
    "not-merged",
    "conflict",
    "missing-method",
    "opinion",
    "not-adopted",
}


# Golden V1 is language-neutral at the concept level, while product prose is Chinese.
# These aliases prevent a correct Chinese explanation from failing an English surface marker.
_CRITERION_ALIASES: dict[str, tuple[str, ...]] = {
    "formatting": ("格式", "样式"),
    "not-affected": ("未受影响", "不受影响", "不在受影响范围", "不在漏洞影响范围"),
    "theme": ("主题", "主题样式"),
    "streamable-http": ("streamable http", "流式 http"),
    "session": ("会话", "会话恢复", "恢复语义"),
    "proposal": ("提案", "草案", "讨论中的方案"),
    "not-merged": ("未合并", "尚未合并", "没有合并", "未发布", "尚未发布", "没有已发布规范"),
    "allowlist": ("白名单", "允许列表", "工具白名单"),
    "docs": ("文档",),
    "typo": ("拼写", "措辞"),
    "provider": ("提供方", "模型提供方"),
    "json-mode": ("json 模式",),
    "validation": ("校验", "验证", "api 错误"),
    "benchmark": ("基准", "基准测试", "评测"),
    "no-api-change": ("api 未变", "接口未变", "api 没有变化", "接口没有变化"),
    "context": ("上下文", "上下文窗口"),
    "unconfigured": ("未配置", "没有配置", "当前未使用"),
    "restart": ("重启",),
    "recovery": ("恢复", "持久化恢复"),
    "conflict": ("冲突", "不一致", "对应另一个标签", "不是目标标签"),
    "maintenance": ("维护", "维护模式"),
    "no-features": ("没有新功能", "无新功能"),
    "trajectory": ("轨迹", "执行轨迹"),
    "tool-selection": ("工具选择",),
    "evaluation": ("评测", "评估"),
    "leaderboard": ("排行榜",),
    "missing-method": ("没有数据集", "缺少数据集", "没有 rubric", "缺少 rubric", "不可复现"),
    "evidence-trail": ("证据轨迹", "证据链"),
    "opinion": ("观点", "意见"),
    "tool-guard": ("tool guard", "工具权限", "工具防护", "工具边界"),
    "approval": ("审批", "批准"),
    "spelling": ("拼写",),
    "rss": ("rss 解析", "rss 解析器"),
    "timestamp": ("更新时间", "时间戳"),
    "regression": ("回归",),
    "marketing": ("营销",),
    "unchanged": ("未变", "不变", "没有变化"),
    "pricing": ("定价", "价格"),
    "malware": ("恶意软件",),
    "not-used": ("未使用", "没有使用", "项目没有使用"),
    "supply-chain": ("供应链", "供应链攻击", "供应链风险"),
    "license": ("许可证", "许可协议", "授权协议"),
    "not-adopted": ("未采用", "尚未采用", "未发布", "仍在讨论"),
    "path-traversal": ("路径穿越", "目录穿越"),
    "api-service": ("api 服务",),
    "provider-adapter": ("提供方适配器", "provider 适配器", "模型适配器"),
    "agent-eval": ("agent 评测", "agent 评估"),
    "observability": ("可观测", "可观测性"),
    "evidence": ("证据验证", "证据核验", "证据"),
    "collection": ("来源采集", "信号采集", "采集"),
    "revision": ("修订", "更新内容", "更新时间"),
    "retries": ("重试",),
    "compliance": ("合规",),
    "no-migration": ("不迁移", "暂不迁移", "不引入", "暂不实现"),
    "error-handling": ("错误处理", "异常处理"),
    "optional-eval": ("可选评测", "仅用于评测", "作为评测"),
    "watch-roadmap": ("跟踪", "关注后续", "维护路线"),
    "compare-eval": ("对比评测", "比较评测", "对比"),
    "compare-design": ("对比设计", "比较设计", "对照"),
    "review-diff": ("diff", "差异", "提交差异"),
    "adapter": ("适配器", "适配层"),
    "pin": ("锁定", "固定版本", "pin"),
    "no-immediate": ("不立即", "暂不", "无需立即"),
    "audit-roots": ("审计", "允许根目录", "资源根目录"),
}

_FORBIDDEN_MARKER_ALIASES: dict[str, tuple[str, ...]] = {
    "breaking change": ("破坏性变更", "不兼容变更"),
    "must upgrade": ("必须升级",),
    "signalharness is vulnerable": ("signalharness 存在漏洞", "signalharness 受漏洞影响"),
    "currently affected": ("当前受影响", "目前受影响"),
    "breaking": ("破坏性", "不兼容"),
    "security issue": ("安全问题", "安全漏洞"),
    "requires migration": ("需要迁移", "必须迁移"),
    "signalharness is definitely vulnerable": ("signalharness 确定存在漏洞", "signalharness 肯定受影响"),
    "mcp now requires outputschema": ("mcp 现在要求 outputschema", "mcp 已要求 outputschema"),
    "the spec has changed": ("规范已经改变", "规范已变更"),
    "langgraph already enforces the allowlist": ("langgraph 已经强制白名单", "langgraph 已实施允许列表"),
    "protocol behavior changed": ("协议行为已经改变", "协议行为已变更"),
    "migration required": ("必须迁移", "需要迁移"),
    "signalharness must migrate": ("signalharness 必须迁移",),
    "api compatibility changed": ("api 兼容性已经改变", "api 兼容性已变更"),
    "affects the current provider adapter": ("影响当前提供方适配器", "影响当前 provider 适配器"),
    "must switch models": ("必须切换模型",),
    "the migration helper is confirmed released": ("迁移辅助已确认发布", "迁移工具已经确认发布"),
    "runtime architecture changed": ("运行时架构已经改变", "运行时架构已变更"),
    "signalharness needs migration": ("signalharness 需要迁移",),
    "proves the model is better": ("证明模型更好", "证明该模型更强"),
    "this is a new protocol requirement": ("这是新的协议要求",),
    "requires signalharness changes": ("要求 signalharness 修改", "需要修改 signalharness"),
    "runtime behavior changed": ("运行时行为已经改变", "运行时行为已变更"),
    "collection is broken": ("采集已经坏了", "采集不可用", "采集系统故障"),
    "parser migration required": ("解析器必须迁移", "需要迁移解析器"),
    "affects signalharness runtime": ("影响 signalharness 运行时",),
    "signalharness is compromised": ("signalharness 已被攻陷", "signalharness 已被入侵"),
    "the license has already changed": ("许可证已经改变", "许可协议已变更"),
    "the released mcp spec already requires this behavior": ("已发布的 mcp 规范已经要求此行为",),
    "upgrade production immediately": ("立即升级生产", "立刻升级生产"),
    "ignore the advisory": ("忽略该安全通告", "忽略该漏洞通告"),
    "downgrade": ("降级",),
    "emergency upgrade": ("紧急升级",),
    "migrate production now": ("现在迁移生产", "立即迁移生产"),
    "emergency patch signalharness": ("紧急修补 signalharness", "立即修补 signalharness"),
    "install 2.99.0": ("安装 2.99.0",),
    "change production behavior before verification": ("验证前修改生产行为", "未验证就修改生产行为"),
}


class ConceptCriterion(BaseModel):
    """One semantic concept represented by several acceptable surface markers."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1)
    any_of: list[str] = Field(min_length=1)

    @field_validator("any_of")
    @classmethod
    def _strip_markers(cls, values: list[str]) -> list[str]:
        cleaned = [value.strip() for value in values if value.strip()]
        if not cleaned:
            raise ValueError("concept criterion needs at least one non-empty marker")
        return list(dict.fromkeys(cleaned))


class CapabilityEvidence(BaseModel):
    """Curated shared evidence packet used by every semantic variant."""

    model_config = ConfigDict(extra="forbid")

    context_summary: str = Field(min_length=1)
    confidence: float = Field(ge=0, le=1)
    source_quality: SourceQuality
    evidence_urls: list[str] = Field(default_factory=list)
    uncertainty: str = ""
    unsupported_claims: list[str] = Field(default_factory=list)


class TrajectoryExpectation(BaseModel):
    """Optional behavior assertions for a full runtime Trace."""

    model_config = ConfigDict(extra="forbid")

    required_agents: list[str] = Field(default_factory=list)
    forbidden_agents: list[str] = Field(default_factory=list)
    required_tools: list[str] = Field(default_factory=list)
    forbidden_tools: list[str] = Field(default_factory=list)
    max_tool_requests: int | None = Field(default=None, ge=0)
    require_no_fallback: bool = False
    require_schema_valid: bool = False


class CapabilityExpectation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    acceptable_decisions: list[SignalDecision] = Field(min_length=1)
    expected_category: str = Field(min_length=1)
    relevance_grade: int = Field(ge=0, le=3)
    must_include_facts: list[ConceptCriterion] = Field(default_factory=list)
    project_concepts: list[ConceptCriterion] = Field(default_factory=list)
    acceptable_action_concepts: list[ConceptCriterion] = Field(default_factory=list)
    forbidden_claims: list[str] = Field(default_factory=list)
    forbidden_actions: list[str] = Field(default_factory=list)
    require_uncertainty: bool = False
    require_chinese_narrative: bool = True
    trajectory: TrajectoryExpectation = Field(default_factory=TrajectoryExpectation)


class CapabilityCase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    tags: list[str] = Field(default_factory=list)
    truth_status: TruthStatus
    event: SignalEvent
    evidence: CapabilityEvidence
    expected: CapabilityExpectation

    @field_validator("event")
    @classmethod
    def _case_id_matches_event(cls, event: SignalEvent, info: Any) -> SignalEvent:
        case_id = info.data.get("id") if hasattr(info, "data") else None
        if case_id and event.event_id != case_id:
            raise ValueError("capability case id must equal event.event_id")
        return event


class CapabilityThresholds(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision_acceptance: float = Field(default=0.75, ge=0, le=1)
    category_accuracy: float = Field(default=0.75, ge=0, le=1)
    ndcg_at_5: float = Field(default=0.75, ge=0, le=1)
    ndcg_at_10: float = Field(default=0.80, ge=0, le=1)
    fact_coverage: float = Field(default=0.65, ge=0, le=1)
    project_specificity: float = Field(default=0.65, ge=0, le=1)
    action_coverage: float = Field(default=0.60, ge=0, le=1)
    uncertainty_pass_rate: float = Field(default=0.80, ge=0, le=1)
    forbidden_claim_pass_rate: float = Field(default=0.95, ge=0, le=1)
    internal_leakage_pass_rate: float = Field(default=1.0, ge=0, le=1)
    language_pass_rate: float = Field(default=0.90, ge=0, le=1)
    hard_negative_accuracy: float = Field(default=0.85, ge=0, le=1)


class CapabilitySuite(BaseModel):
    model_config = ConfigDict(extra="forbid")

    suite: str = Field(min_length=1)
    description: str = ""
    dataset_role: Literal["capability"] = "capability"
    thresholds: CapabilityThresholds = Field(default_factory=CapabilityThresholds)
    cases: list[CapabilityCase] = Field(min_length=1)

    @field_validator("cases")
    @classmethod
    def _unique_case_ids(cls, cases: list[CapabilityCase]) -> list[CapabilityCase]:
        ids = [case.id for case in cases]
        if len(ids) != len(set(ids)):
            raise ValueError("capability case ids must be unique")
        return cases


class CapabilityCaseScore(BaseModel):
    model_config = ConfigDict(extra="forbid")

    case_id: str
    decision_pass: bool
    category_pass: bool
    fact_coverage: float | None = Field(default=None, ge=0, le=1)
    project_specificity: float | None = Field(default=None, ge=0, le=1)
    action_coverage: float | None = Field(default=None, ge=0, le=1)
    uncertainty_pass: bool | None = None
    forbidden_claims_pass: bool
    forbidden_actions_pass: bool
    internal_leakage_pass: bool
    language_pass: bool
    hard_negative_pass: bool | None = None


class CapabilityObservedOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    case_id: str
    decision: SignalDecision
    impact_score: float = Field(ge=0, le=100)
    what_changed_zh: str = ""
    why_relevant_zh: str = ""
    recommended_actions_zh: list[str] = Field(default_factory=list)


class CapabilityCallDiagnostic(BaseModel):
    model_config = ConfigDict(extra="forbid")

    agent: str
    schema_valid: bool | None = None
    fallback_used: bool = False
    retry_count: int = Field(ge=0)
    duration_ms: int = Field(ge=0)
    total_tokens: int = Field(ge=0)
    estimated_cost_usd: float = Field(ge=0)
    error: str | None = None
    schema_error: str | None = None


class CapabilityTrialCheckpoint(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: str = "capability-trial-v1"
    suite: str
    mode: RunMode
    provider: str
    model: str
    shared_context_hash: str
    experiment_signature: str
    batch_size: int = Field(ge=1)
    variant: Literal["single", "split"]
    trial_index: int = Field(ge=1)
    assessments: list[SignalAssessment]
    trace: list[TraceStep]
    coverage_valid: bool
    coverage_diagnostics: list[str] = Field(default_factory=list)


class CapabilityTrialSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    trial_index: int = Field(ge=1)
    coverage_valid: bool
    coverage_diagnostics: list[str] = Field(default_factory=list)
    llm_call_count: int = Field(ge=0)
    schema_valid_rate: float = Field(ge=0, le=1)
    fallback_rate: float = Field(ge=0, le=1)
    llm_latency_ms: int = Field(ge=0)
    total_tokens: int = Field(ge=0)
    estimated_cost_usd: float = Field(ge=0)
    calls: list[CapabilityCallDiagnostic] = Field(default_factory=list)


class TrajectoryEvalSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    evaluated_cases: int = Field(ge=0)
    passed_cases: int = Field(ge=0)
    pass_rate: float = Field(ge=0, le=1)
    failures: dict[str, list[str]] = Field(default_factory=dict)


class CapabilityVariantSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    variant: str
    shared_context_hash: str
    case_count: int = Field(ge=1)
    trials: int = Field(ge=1)
    coverage_valid_rate: float = Field(ge=0, le=1)
    schema_valid_rate: float = Field(ge=0, le=1)
    decision_acceptance: float = Field(ge=0, le=1)
    category_accuracy: float = Field(ge=0, le=1)
    ndcg_at_5: float = Field(ge=0, le=1)
    ndcg_at_10: float = Field(ge=0, le=1)
    fact_coverage: float = Field(ge=0, le=1)
    project_specificity: float = Field(ge=0, le=1)
    action_coverage: float = Field(ge=0, le=1)
    uncertainty_pass_rate: float = Field(ge=0, le=1)
    forbidden_claim_pass_rate: float = Field(ge=0, le=1)
    internal_leakage_pass_rate: float = Field(ge=0, le=1)
    language_pass_rate: float = Field(ge=0, le=1)
    hard_negative_accuracy: float = Field(ge=0, le=1)
    decision_consistency_rate: float = Field(ge=0, le=1)
    llm_call_count: int = Field(ge=0)
    llm_latency_ms: int = Field(ge=0)
    total_tokens: int = Field(ge=0)
    estimated_cost_usd: float = Field(ge=0)
    fallback_rate: float = Field(ge=0, le=1)
    passed: bool
    case_scores: list[CapabilityCaseScore] = Field(default_factory=list)
    observed_outputs: list[CapabilityObservedOutput] = Field(default_factory=list)
    trial_summaries: list[CapabilityTrialSummary] = Field(default_factory=list)


class CapabilityComparisonSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    suite: str
    case_count: int = Field(ge=1)
    trials: int = Field(ge=1)
    batch_size: int = Field(ge=1)
    mode: RunMode
    provider: str
    model: str
    eval_version: str = CAPABILITY_EVAL_VERSION
    grader_version: str = CAPABILITY_GRADER_VERSION
    scoring_version: str = GUARDED_SCORING_VERSION
    presentation_version: str = PRESENTATION_VERSION
    evaluation_source: Literal["provider_generation", "checkpoint_regrade"] = "provider_generation"
    prompt_version: str = PROMPT_VERSION
    experiment_signature: str = ""
    shared_context_hash: str
    variants: list[CapabilityVariantSummary]
    comparison_valid: bool
    recommendation_state: RecommendationState
    recommendation_reason: str


@dataclass
class _VariantTrial:
    assessments: list[SignalAssessment]
    trace: list[TraceStep]
    coverage_valid: bool
    coverage_diagnostics: list[str]
    case_scores: list[CapabilityCaseScore]
    metrics: dict[str, float]


def load_capability_suite(path: str | Path) -> CapabilitySuite:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    return CapabilitySuite.model_validate(payload)


def _routes_for(events: list[SignalEvent], project_profile: dict[str, Any]) -> SupervisorOutput:
    classifier = ClassifierAgent()
    routes = []
    for event in events:
        category = classifier.run(event, project_profile).category
        routes.append(
            SupervisorRoute(
                event_id=event.event_id,
                category=category,
                analyze=True,
                routing_reason="Deterministic shared-evidence capability-eval route.",
                required_agents=["context_evidence", "impact", "action"],
            )
        )
    return SupervisorOutput(routes=routes, batch_summary="Shared deterministic eval route.")


def _evidence_for(suite: CapabilitySuite) -> ContextEvidenceOutput:
    return ContextEvidenceOutput(
        results=[
            ContextEvidenceItem(
                event_id=case.id,
                evidence_urls=case.evidence.evidence_urls,
                context_summary=case.evidence.context_summary,
                confidence=case.evidence.confidence,
                source_quality=case.evidence.source_quality,
                unsupported_claims=case.evidence.unsupported_claims,
                uncertainty=case.evidence.uncertainty,
                source_types_observed=[case.event.source_type],
            )
            for case in suite.cases
        ]
    )


def _shared_context_hash(
    *,
    events: list[SignalEvent],
    routes: SupervisorOutput,
    evidence: ContextEvidenceOutput,
    project_profile: dict[str, Any],
) -> str:
    payload = {
        "events": [event.model_dump(mode="json") for event in events],
        "routes": routes.model_dump(mode="json"),
        "evidence": evidence.model_dump(mode="json"),
        "project_profile": project_profile,
    }
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _attach_narrative(
    assessments: list[SignalAssessment],
    *,
    report_zh: str,
    narratives: dict[str, tuple[str, str, list[str]]],
) -> list[SignalAssessment]:
    attached = []
    for assessment in assessments:
        narrative = narratives.get(assessment.event_id)
        if narrative is None:
            attached.append(assessment)
            continue
        what, why, actions = narrative
        attached.append(
            assessment.model_copy(
                update={
                    "what_changed_zh": what,
                    "why_relevant_zh": why,
                    "action_items_zh": sanitize_user_facing_actions(actions),
                    "report_summary_zh": report_zh,
                }
            )
        )
    return attached


def _single_call(
    *,
    events: list[SignalEvent],
    routes: SupervisorOutput,
    evidence: ContextEvidenceOutput,
    project_profile: dict[str, Any],
    policy: dict[str, Any],
) -> Any:
    payload = {
        "events": [event.model_dump(mode="json") for event in events],
        "routes": routes.model_dump(mode="json"),
        "evidence": evidence.model_dump(mode="json"),
        "contract": [
            "Use only the supplied shared evidence; do not invent or fetch additional facts.",
            "In one pass, produce impact, bounded review actions, and natural Simplified-Chinese product copy.",
            "Never emit a final score. Python computes scores and decisions from the same guarded policy used by the split stack.",
            "Preserve uncertainty and do not turn an issue/proposal/unverified report into an implemented fact.",
            "Return exactly one result per event_id and copy event_id values verbatim.",
        ],
    }
    return build_agent_call(
        agent_name="SharedEvidenceSingleAgentBaseline",
        output_model=SharedEvidenceSingleOutput,
        dynamic_payload=payload,
        input_count=len(events),
        project_context={"project_profile": project_profile, "policy": policy},
        volatile_metadata={"eval_role": "fair_single_agent_baseline"},
    )


def _single_fallback(
    *,
    events: list[SignalEvent],
    routes: SupervisorOutput,
    evidence: ContextEvidenceOutput,
    project_profile: dict[str, Any],
    policy: dict[str, Any],
) -> SharedEvidenceSingleOutput:
    impact_agent = ImpactAnalystAgent()
    action_agent = ActionPlannerAgent()
    narrative_agent = ProjectNarrativeAgent()
    impact = impact_agent.fallback(events, project_profile, policy)
    action = action_agent.fallback(events, impact)
    assessments, _ = guarded_assessments(
        events,
        routes=routes,
        evidence=evidence,
        impact=impact,
        action=action,
        project_profile=project_profile,
        policy=policy,
        noise_assessments=[],
        seen_hashes=None,
        feedback_history=(),
    )
    narrative = narrative_agent.fallback(
        events, assessments, project_profile=project_profile
    )
    impact_by_id = {item.event_id: item for item in impact.results}
    action_by_id = {item.event_id: item for item in action.results}
    narrative_by_id = {item.event_id: item for item in narrative.results}
    return SharedEvidenceSingleOutput(
        report_zh=narrative.report_zh,
        results=[
            SharedEvidenceSingleItem(
                event_id=event.event_id,
                impact=impact_by_id[event.event_id],
                action=action_by_id[event.event_id],
                what_changed_zh=narrative_by_id[event.event_id].what_changed_zh,
                why_relevant_zh=narrative_by_id[event.event_id].why_relevant_zh,
                recommended_actions_zh=narrative_by_id[event.event_id].recommended_actions_zh,
            )
            for event in events
        ],
    )


async def _run_single_trial(
    *,
    provider: AgentProvider,
    mode: RunMode,
    suite: CapabilitySuite,
    project_profile: dict[str, Any],
    policy: dict[str, Any],
    events: list[SignalEvent],
    routes: SupervisorOutput,
    evidence: ContextEvidenceOutput,
) -> tuple[list[SignalAssessment], list[TraceStep], bool, list[str]]:
    trace = TraceRecorder()
    invoker = AgentInvoker(
        provider=provider,
        mode=mode,
        trace=trace,
        limits=AgentLoopLimits(),
    )
    output, trace_index = await invoker.invoke(
        _single_call(
            events=events,
            routes=routes,
            evidence=evidence,
            project_profile=project_profile,
            policy=policy,
        ),
        SharedEvidenceSingleOutput,
        lambda: _single_fallback(
            events=events,
            routes=routes,
            evidence=evidence,
            project_profile=project_profile,
            policy=policy,
        ),
        event_ids=[event.event_id for event in events],
    )
    expected_ids = [case.id for case in suite.cases]
    expected_set = set(expected_ids)
    by_id = {
        item.event_id: item
        for item in output.results
        if item.event_id in expected_set
    }
    missing_ids = [event_id for event_id in expected_ids if event_id not in by_id]
    if missing_ids and not trace.steps[trace_index].fallback_used:
        missing_set = set(missing_ids)
        repair_events = [event for event in events if event.event_id in missing_set]
        repair_routes = _subset_routes(routes, missing_set)
        repair_evidence = _subset_evidence(evidence, missing_set)
        repair_call = _single_call(
            events=repair_events,
            routes=repair_routes,
            evidence=repair_evidence,
            project_profile=project_profile,
            policy=policy,
        )
        repair_call = replace(
            repair_call,
            user_prompt=(
                repair_call.user_prompt.rstrip()
                + "\n\nCOVERAGE REPAIR: Return exactly one results item for each "
                + f"missing event_id in this request: {missing_ids}. "
                + "Copy event_id verbatim and return no extra event IDs."
            ),
        )
        repaired, _ = await invoker.invoke(
            repair_call,
            SharedEvidenceSingleOutput,
            lambda: _single_fallback(
                events=repair_events,
                routes=repair_routes,
                evidence=repair_evidence,
                project_profile=project_profile,
                policy=policy,
            ),
            event_ids=missing_ids,
        )
        for item in repaired.results:
            if item.event_id in missing_set and item.event_id not in by_id:
                by_id[item.event_id] = item
    actual_ids = [item.event_id for item in output.results]
    actual_set = set(actual_ids)
    coverage_valid = set(by_id) == expected_set and len(by_id) == len(expected_ids)
    coverage_diagnostics: list[str] = []
    if not coverage_valid:
        missing = sorted(expected_set - set(by_id))
        extra = sorted(actual_set - expected_set)
        duplicate_count = max(0, len(actual_ids) - len(actual_set))
        coverage_diagnostics.append(
            f"single missing={missing} extra={extra} duplicates={duplicate_count}"
        )
    impact = ImpactOutput(
        results=[by_id[event.event_id].impact for event in events if event.event_id in by_id]
    )
    action = ActionOutput(
        results=[by_id[event.event_id].action for event in events if event.event_id in by_id]
    )
    if not coverage_valid:
        fallback = _single_fallback(
            events=events,
            routes=routes,
            evidence=evidence,
            project_profile=project_profile,
            policy=policy,
        )
        by_id = {item.event_id: item for item in fallback.results}
        impact = ImpactOutput(results=[by_id[event.event_id].impact for event in events])
        action = ActionOutput(results=[by_id[event.event_id].action for event in events])
        report_zh = fallback.report_zh
    else:
        report_zh = output.report_zh
    assessments, _ = guarded_assessments(
        events,
        routes=routes,
        evidence=evidence,
        impact=impact,
        action=action,
        project_profile=project_profile,
        policy=policy,
        noise_assessments=[],
        seen_hashes=None,
        feedback_history=(),
    )
    narratives = {
        event_id: (
            item.what_changed_zh,
            item.why_relevant_zh,
            sanitize_user_facing_actions(
                item.recommended_actions_zh or item.action.action_items
            ),
        )
        for event_id, item in by_id.items()
    }
    return (
        _attach_narrative(assessments, report_zh=report_zh, narratives=narratives),
        list(trace.steps),
        coverage_valid,
        coverage_diagnostics,
    )


async def _run_split_trial(
    *,
    provider: AgentProvider,
    mode: RunMode,
    suite: CapabilitySuite,
    project_profile: dict[str, Any],
    policy: dict[str, Any],
    events: list[SignalEvent],
    routes: SupervisorOutput,
    evidence: ContextEvidenceOutput,
) -> tuple[list[SignalAssessment], list[TraceStep], bool, list[str]]:
    trace = TraceRecorder()
    invoker = AgentInvoker(
        provider=provider,
        mode=mode,
        trace=trace,
        limits=AgentLoopLimits(),
    )
    impact_agent = ImpactAnalystAgent()
    action_agent = ActionPlannerAgent()
    narrative_agent = ProjectNarrativeAgent()
    impact, _ = await invoker.invoke(
        impact_agent.build_call(
            events,
            project_profile,
            routes,
            evidence,
            policy=policy,
            volatile_metadata={"eval_role": "split_semantic_stack"},
        ),
        ImpactOutput,
        lambda: impact_agent.fallback(events, project_profile, policy),
        event_ids=[event.event_id for event in events],
    )
    action, _ = await invoker.invoke(
        action_agent.build_call(
            events,
            impact,
            project_profile=project_profile,
            policy=policy,
            volatile_metadata={"eval_role": "split_semantic_stack"},
        ),
        ActionOutput,
        lambda: action_agent.fallback(events, impact),
        event_ids=[event.event_id for event in events],
    )
    expected_ids = {case.id for case in suite.cases}
    impact_ids = {item.event_id for item in impact.results}
    action_ids = {item.event_id for item in action.results}
    coverage_valid = impact_ids == expected_ids and action_ids == expected_ids
    coverage_diagnostics: list[str] = []
    if impact_ids != expected_ids:
        coverage_diagnostics.append(
            f"impact missing={sorted(expected_ids - impact_ids)} extra={sorted(impact_ids - expected_ids)}"
        )
    if action_ids != expected_ids:
        coverage_diagnostics.append(
            f"action missing={sorted(expected_ids - action_ids)} extra={sorted(action_ids - expected_ids)}"
        )
    if not coverage_valid:
        impact = impact_agent.fallback(events, project_profile, policy)
        action = action_agent.fallback(events, impact)
    assessments, _ = guarded_assessments(
        events,
        routes=routes,
        evidence=evidence,
        impact=impact,
        action=action,
        project_profile=project_profile,
        policy=policy,
        noise_assessments=[],
        seen_hashes=None,
        feedback_history=(),
    )
    narrative, narrative_trace_index = await invoker.invoke(
        narrative_agent.build_call(
            events,
            assessments,
            project_profile=project_profile,
            policy=policy,
            volatile_metadata={"eval_role": "split_semantic_stack"},
        ),
        ProjectNarrativeOutput,
        lambda: narrative_agent.fallback(
            events, assessments, project_profile=project_profile
        ),
        event_ids=[event.event_id for event in events],
    )
    narrative_by_id = {
        item.event_id: item
        for item in narrative.results
        if item.event_id in expected_ids
    }
    missing_narrative_ids = sorted(expected_ids - set(narrative_by_id))
    if missing_narrative_ids and not trace.steps[narrative_trace_index].fallback_used:
        missing_set = set(missing_narrative_ids)
        repair_events = [event for event in events if event.event_id in missing_set]
        repair_assessments = [
            item for item in assessments if item.event_id in missing_set
        ]
        repair_call = narrative_agent.build_call(
            repair_events,
            repair_assessments,
            project_profile=project_profile,
            policy=policy,
            volatile_metadata={
                "eval_role": "split_semantic_stack",
                "coverage_repair": True,
                "missing_event_ids": missing_narrative_ids,
            },
        )
        repair_call = replace(
            repair_call,
            user_prompt=(
                repair_call.user_prompt.rstrip()
                + "\n\nCOVERAGE REPAIR: Return exactly one results item for each "
                + f"missing event_id in this request: {missing_narrative_ids}. "
                + "Copy event_id verbatim and return no extra event IDs."
            ),
        )
        repaired, _ = await invoker.invoke(
            repair_call,
            ProjectNarrativeOutput,
            lambda: narrative_agent.fallback(
                repair_events, repair_assessments, project_profile=project_profile
            ),
            event_ids=missing_narrative_ids,
        )
        for item in repaired.results:
            if item.event_id in missing_set and item.event_id not in narrative_by_id:
                narrative_by_id[item.event_id] = item
        narrative = narrative.model_copy(
            update={
                "results": [
                    narrative_by_id[event.event_id]
                    for event in events
                    if event.event_id in narrative_by_id
                ]
            }
        )
    narrative_ids = set(narrative_by_id)
    coverage_valid = coverage_valid and narrative_ids == expected_ids
    if narrative_ids != expected_ids:
        coverage_diagnostics.append(
            f"narrative missing={sorted(expected_ids - narrative_ids)} extra={sorted(narrative_ids - expected_ids)}"
        )
        narrative = narrative_agent.fallback(
            events, assessments, project_profile=project_profile
        )
    narratives = {
        item.event_id: (
            item.what_changed_zh,
            item.why_relevant_zh,
            item.recommended_actions_zh,
        )
        for item in narrative.results
    }
    return (
        _attach_narrative(
            assessments,
            report_zh=narrative.report_zh,
            narratives=narratives,
        ),
        list(trace.steps),
        coverage_valid,
        coverage_diagnostics,
    )


def _normalize_text(value: str) -> str:
    return " ".join(value.lower().split())


def _criterion_match(text: str, criterion: ConceptCriterion) -> bool:
    normalized = _normalize_text(text)
    markers = [*criterion.any_of, *_CRITERION_ALIASES.get(criterion.name, ())]
    return any(_normalize_text(marker) in normalized for marker in markers)


def _criterion_coverage(text: str, criteria: list[ConceptCriterion]) -> float | None:
    if not criteria:
        return None
    return sum(_criterion_match(text, criterion) for criterion in criteria) / len(criteria)


def _contains_forbidden(text: str, markers: Iterable[str]) -> bool:
    normalized = _normalize_text(text)
    for marker in markers:
        if not marker.strip():
            continue
        normalized_marker = _normalize_text(marker)
        expanded = (marker, *_FORBIDDEN_MARKER_ALIASES.get(normalized_marker, ()))
        if any(_normalize_text(item) in normalized for item in expanded):
            return True
    return False


def _has_internal_leakage(text: str) -> bool:
    if _contains_forbidden(text, _INTERNAL_LEAKAGE_MARKERS):
        return True
    normalized = " ".join(text.lower().split())
    # Avoid treating normal Chinese such as "迁移影响分析" as the debug field "影响分".
    if re.search(r"(?:^|[\s，。；,:：])影响分(?:数|值)?\s*(?:[:：=]|为|是|\d)", normalized):
        return True
    if re.search(
        r"判定为\s*[‘’'\"“”]?(?:save|alert|ignore|action_required)", normalized
    ):
        return True
    return False


def _has_chinese(value: str, *, minimum: int = 6) -> bool:
    count = sum("\u4e00" <= char <= "\u9fff" for char in value)
    return count >= minimum


def _uncertainty_preserved(case: CapabilityCase, text: str) -> bool:
    if _contains_forbidden(text, _UNCERTAINTY_MARKERS):
        return True
    return any(
        criterion.name in _UNCERTAINTY_CRITERION_NAMES
        and _criterion_match(text, criterion)
        for criterion in case.expected.must_include_facts
    )


def _case_score(case: CapabilityCase, assessment: SignalAssessment | None) -> CapabilityCaseScore:
    if assessment is None:
        return CapabilityCaseScore(
            case_id=case.id,
            decision_pass=False,
            category_pass=False,
            fact_coverage=0.0 if case.expected.must_include_facts else None,
            project_specificity=0.0 if case.expected.project_concepts else None,
            action_coverage=0.0 if case.expected.acceptable_action_concepts else None,
            uncertainty_pass=False if case.expected.require_uncertainty else None,
            forbidden_claims_pass=False,
            forbidden_actions_pass=False,
            internal_leakage_pass=False,
            language_pass=False,
            hard_negative_pass=False if case.expected.relevance_grade == 0 else None,
        )
    what = assessment.what_changed_zh or ""
    why = assessment.why_relevant_zh or ""
    actions = sanitize_user_facing_actions(
        assessment.action_items_zh or assessment.action_items
    )
    action_text = " ".join(actions)
    combined = " ".join([what, why, action_text])
    uncertainty_pass = None
    if case.expected.require_uncertainty:
        uncertainty_pass = _uncertainty_preserved(case, combined)
    language_pass = True
    if case.expected.require_chinese_narrative:
        language_pass = _has_chinese(what) and _has_chinese(why)
        if actions:
            language_pass = language_pass and _has_chinese(action_text)
    hard_negative_pass = None
    if case.expected.relevance_grade == 0:
        hard_negative_pass = assessment.decision in set(case.expected.acceptable_decisions)
    return CapabilityCaseScore(
        case_id=case.id,
        decision_pass=assessment.decision in set(case.expected.acceptable_decisions),
        category_pass=assessment.category.value == case.expected.expected_category,
        fact_coverage=_criterion_coverage(what, case.expected.must_include_facts),
        project_specificity=_criterion_coverage(why, case.expected.project_concepts),
        action_coverage=_criterion_coverage(
            action_text, case.expected.acceptable_action_concepts
        ),
        uncertainty_pass=uncertainty_pass,
        forbidden_claims_pass=not _contains_forbidden(
            combined, case.expected.forbidden_claims
        ),
        forbidden_actions_pass=not _contains_forbidden(
            action_text, case.expected.forbidden_actions
        ),
        internal_leakage_pass=not _has_internal_leakage(combined),
        language_pass=language_pass,
        hard_negative_pass=hard_negative_pass,
    )


def _mean_optional(values: Iterable[float | None], *, default: float = 1.0) -> float:
    present = [value for value in values if value is not None]
    return mean(present) if present else default


def _rate(values: Iterable[bool | None], *, default: float = 1.0) -> float:
    present = [value for value in values if value is not None]
    return (sum(bool(value) for value in present) / len(present)) if present else default


def _ndcg(
    *,
    assessments: list[SignalAssessment],
    suite: CapabilitySuite,
    k: int,
) -> float:
    relevance = {case.id: case.expected.relevance_grade for case in suite.cases}
    ranked = sorted(
        (item for item in assessments if item.event_id in relevance),
        key=lambda item: (-item.impact_score, item.event_id),
    )
    actual_grades = [relevance[item.event_id] for item in ranked[:k]]
    ideal_grades = sorted(relevance.values(), reverse=True)[:k]

    def dcg(grades: list[int]) -> float:
        return float(
            sum(
                (2**grade - 1) / math.log2(index + 2)
                for index, grade in enumerate(grades)
            )
        )

    ideal = dcg(ideal_grades)
    return (dcg(actual_grades) / ideal) if ideal > 0 else 1.0


def _trial_metrics(
    *,
    assessments: list[SignalAssessment],
    suite: CapabilitySuite,
) -> tuple[list[CapabilityCaseScore], dict[str, float]]:
    by_id = {item.event_id: item for item in assessments}
    scores = [_case_score(case, by_id.get(case.id)) for case in suite.cases]
    metrics = {
        "decision_acceptance": mean(score.decision_pass for score in scores),
        "category_accuracy": mean(score.category_pass for score in scores),
        "ndcg_at_5": _ndcg(assessments=assessments, suite=suite, k=5),
        "ndcg_at_10": _ndcg(assessments=assessments, suite=suite, k=10),
        "fact_coverage": _mean_optional(score.fact_coverage for score in scores),
        "project_specificity": _mean_optional(
            score.project_specificity for score in scores
        ),
        "action_coverage": _mean_optional(score.action_coverage for score in scores),
        "uncertainty_pass_rate": _rate(score.uncertainty_pass for score in scores),
        "forbidden_claim_pass_rate": mean(
            score.forbidden_claims_pass and score.forbidden_actions_pass for score in scores
        ),
        "internal_leakage_pass_rate": mean(score.internal_leakage_pass for score in scores),
        "language_pass_rate": mean(score.language_pass for score in scores),
        "hard_negative_accuracy": _rate(score.hard_negative_pass for score in scores),
    }
    return scores, metrics


def regrade_capability_assessments(
    assessments: list[SignalAssessment],
    *,
    suite: CapabilitySuite,
    project_profile: dict[str, Any],
    policy: dict[str, Any],
) -> list[SignalAssessment]:
    """Re-run only deterministic scoring over frozen semantic outputs."""

    case_by_id = {case.id: case for case in suite.cases}
    rescored: list[SignalAssessment] = []
    for assessment in assessments:
        case = case_by_id.get(assessment.event_id)
        if case is None:
            rescored.append(assessment)
            continue
        scored = compute_guarded_score_decision(
            case.event,
            category=assessment.category,
            analyze=True,
            source_quality=assessment.source_quality,
            evidence_confidence=assessment.confidence,
            evidence_uncertainty=case.evidence.uncertainty,
            evidence_unsupported_claims=case.evidence.unsupported_claims,
            semantic_relevance=assessment.relevance_score,
            project_profile=project_profile,
            policy=policy,
            noise_multiplier=1.0,
            seen_hashes=None,
            feedback_history=(),
        )
        rescored.append(
            assessment.model_copy(
                update={
                    "impact_score": scored.final_score,
                    "decision": scored.decision,
                    "score_breakdown": scored.score_breakdown,
                    "agent_score_breakdown": scored.agent_score_breakdown,
                    "action_items_zh": sanitize_user_facing_actions(
                        assessment.action_items_zh or assessment.action_items
                    ),
                }
            )
        )
    return rescored


def regrade_capability_checkpoints(
    *,
    checkpoint_dir: Path,
    suite: CapabilitySuite,
    project_profile: dict[str, Any],
    policy: dict[str, Any],
    trials: int,
    batch_size: int,
) -> CapabilityComparisonSummary:
    """Re-evaluate frozen real-model semantics under current deterministic scoring.

    This path executes zero provider calls. Generation validity is taken only from
    schema-valid, no-fallback checkpoints whose frozen Event/Profile context matches
    the requested Capability suite. Scoring-policy changes therefore do not waste LLM calls.
    """

    if trials < 1:
        raise ValueError("trials must be positive")
    routes = _routes_for([case.event for case in suite.cases], project_profile)
    evidence = _evidence_for(suite)
    shared_hash = _shared_context_hash(
        events=[case.event for case in suite.cases],
        routes=routes,
        evidence=evidence,
        project_profile=project_profile,
    )
    variant_trials: dict[str, list[_VariantTrial]] = {"single": [], "split": []}
    checkpoints: list[CapabilityTrialCheckpoint] = []
    for variant in ("single", "split"):
        for trial_index in range(1, trials + 1):
            path = _checkpoint_path(
                checkpoint_dir, variant=variant, trial_index=trial_index
            )
            if not path.exists():
                raise ValueError(f"Missing Capability checkpoint: {path}")
            checkpoint = CapabilityTrialCheckpoint.model_validate_json(
                path.read_text(encoding="utf-8")
            )
            if (
                checkpoint.suite != suite.suite
                or checkpoint.shared_context_hash != shared_hash
                or checkpoint.batch_size != batch_size
                or checkpoint.variant != variant
                or checkpoint.trial_index != trial_index
                or not _checkpoint_reusable(checkpoint)
            ):
                raise ValueError(
                    f"Checkpoint is not valid for deterministic regrade: {path}"
                )
            rescored = regrade_capability_assessments(
                checkpoint.assessments,
                suite=suite,
                project_profile=project_profile,
                policy=policy,
            )
            case_scores, metrics = _trial_metrics(assessments=rescored, suite=suite)
            variant_trials[variant].append(
                _VariantTrial(
                    assessments=rescored,
                    trace=checkpoint.trace,
                    coverage_valid=checkpoint.coverage_valid,
                    coverage_diagnostics=checkpoint.coverage_diagnostics,
                    case_scores=case_scores,
                    metrics=metrics,
                )
            )
            checkpoints.append(checkpoint)

    providers = {item.provider for item in checkpoints}
    models = {item.model for item in checkpoints}
    signatures = {item.experiment_signature for item in checkpoints}
    prompt_versions = {
        step.prompt_version
        for checkpoint in checkpoints
        for step in checkpoint.trace
        if step.step == "llm_agent_call" and step.prompt_version
    }
    if len(providers) != 1 or len(models) != 1 or len(signatures) != 1:
        raise ValueError("Capability checkpoints do not share one provider/model experiment")
    if len(prompt_versions) != 1:
        raise ValueError("Capability checkpoints do not share one prompt version")

    variants = [
        _aggregate_variant(
            variant="shared-evidence-single-agent",
            shared_hash=shared_hash,
            suite=suite,
            trials=variant_trials["single"],
        ),
        _aggregate_variant(
            variant="split-impact-action-narrative",
            shared_hash=shared_hash,
            suite=suite,
            trials=variant_trials["split"],
        ),
    ]
    comparison_valid = all(
        item.coverage_valid_rate == 1.0
        and item.schema_valid_rate == 1.0
        and item.fallback_rate == 0.0
        for item in variants
    )
    state: RecommendationState = (
        "baseline_matrix_ready_no_auto_promotion"
        if comparison_valid and trials >= 3
        else "needs_repeated_real_trials"
        if comparison_valid
        else "invalid_provider_or_schema"
    )
    reason = (
        "Frozen schema-valid real-provider semantics were re-scored locally with zero new "
        "model calls. This is valid scoring replay evidence, not a fresh model generation, "
        "and it does not auto-promote an architecture."
    )
    first = checkpoints[0]
    return CapabilityComparisonSummary(
        suite=suite.suite,
        case_count=len(suite.cases),
        trials=trials,
        batch_size=batch_size,
        mode=first.mode,
        provider=next(iter(providers)),
        model=next(iter(models)),
        eval_version=CAPABILITY_EVAL_VERSION,
        grader_version=CAPABILITY_GRADER_VERSION,
        scoring_version=GUARDED_SCORING_VERSION,
        presentation_version=PRESENTATION_VERSION,
        evaluation_source="checkpoint_regrade",
        prompt_version=next(iter(prompt_versions)),
        experiment_signature=next(iter(signatures)),
        shared_context_hash=shared_hash,
        variants=variants,
        comparison_valid=comparison_valid,
        recommendation_state=state,
        recommendation_reason=reason,
    )


def _trajectory_steps_for_case(trace: list[TraceStep], case_id: str) -> list[TraceStep]:
    selected = []
    for step in trace:
        ids = {
            item.strip()
            for item in str(step.input_event_id or "").split(",")
            if item.strip()
        }
        metadata_ids = step.metadata.get("event_ids") if isinstance(step.metadata, dict) else None
        if isinstance(metadata_ids, list):
            ids.update(str(item) for item in metadata_ids)
        if case_id in ids:
            selected.append(step)
    return selected


def evaluate_trajectory_suite(
    *, trace: list[TraceStep], suite: CapabilitySuite
) -> TrajectoryEvalSummary:
    failures: dict[str, list[str]] = {}
    evaluated = 0
    for case in suite.cases:
        expected = case.expected.trajectory
        has_contract = any(
            (
                expected.required_agents,
                expected.forbidden_agents,
                expected.required_tools,
                expected.forbidden_tools,
            )
        ) or expected.max_tool_requests is not None or expected.require_no_fallback or expected.require_schema_valid
        if not has_contract:
            continue
        evaluated += 1
        steps = _trajectory_steps_for_case(trace, case.id)
        agents = {str(step.agent_name or step.agent or "") for step in steps}
        tools = {
            tool
            for step in steps
            for tool in [*step.tools_requested, *step.tools_executed]
        }
        errors: list[str] = []
        for agent in expected.required_agents:
            if agent not in agents:
                errors.append(f"missing_agent:{agent}")
        for agent in expected.forbidden_agents:
            if agent in agents:
                errors.append(f"forbidden_agent:{agent}")
        for tool in expected.required_tools:
            if tool not in tools:
                errors.append(f"missing_tool:{tool}")
        for tool in expected.forbidden_tools:
            if tool in tools:
                errors.append(f"forbidden_tool:{tool}")
        if expected.max_tool_requests is not None:
            requests = sum(len(step.tools_requested) for step in steps)
            if requests > expected.max_tool_requests:
                errors.append(
                    f"tool_request_budget:{requests}>{expected.max_tool_requests}"
                )
        if expected.require_no_fallback and any(step.fallback_used for step in steps):
            errors.append("fallback_used")
        if expected.require_schema_valid and any(
            step.step == "llm_agent_call" and step.schema_valid is not True for step in steps
        ):
            errors.append("schema_invalid")
        if errors:
            failures[case.id] = errors
    passed = evaluated - len(failures)
    return TrajectoryEvalSummary(
        evaluated_cases=evaluated,
        passed_cases=passed,
        pass_rate=(passed / evaluated if evaluated else 1.0),
        failures=failures,
    )


def _operational_metrics(trace: list[TraceStep]) -> tuple[int, int, int, float, float, int]:
    llm = [step for step in trace if step.step == "llm_agent_call"]
    calls = len(llm)
    latency = sum(step.duration_ms for step in llm)
    tokens = sum(step.total_tokens or 0 for step in llm)
    cost = round(sum(step.estimated_cost_usd or 0.0 for step in llm), 8)
    fallback_rate = sum(step.fallback_used for step in llm) / calls if calls else 0.0
    schema_valid = sum(step.schema_valid is True for step in llm)
    return calls, latency, tokens, cost, fallback_rate, schema_valid


def _passes_thresholds(metrics: dict[str, float], thresholds: CapabilityThresholds) -> bool:
    return all(
        metrics[key] >= float(getattr(thresholds, key))
        for key in (
            "decision_acceptance",
            "category_accuracy",
            "ndcg_at_5",
            "ndcg_at_10",
            "fact_coverage",
            "project_specificity",
            "action_coverage",
            "uncertainty_pass_rate",
            "forbidden_claim_pass_rate",
            "internal_leakage_pass_rate",
            "language_pass_rate",
            "hard_negative_accuracy",
        )
    )


def _trial_summary(trial: _VariantTrial, *, trial_index: int) -> CapabilityTrialSummary:
    llm = [step for step in trial.trace if step.step == "llm_agent_call"]
    calls = [
        CapabilityCallDiagnostic(
            agent=str(step.agent_name or step.agent or "unknown"),
            schema_valid=step.schema_valid,
            fallback_used=step.fallback_used,
            retry_count=step.retry_count,
            duration_ms=step.duration_ms,
            total_tokens=step.total_tokens or 0,
            estimated_cost_usd=step.estimated_cost_usd or 0.0,
            error=step.error,
            schema_error=step.schema_error,
        )
        for step in llm
    ]
    count = len(calls)
    return CapabilityTrialSummary(
        trial_index=trial_index,
        coverage_valid=trial.coverage_valid,
        coverage_diagnostics=trial.coverage_diagnostics,
        llm_call_count=count,
        schema_valid_rate=(
            sum(item.schema_valid is True for item in calls) / count if count else 1.0
        ),
        fallback_rate=(sum(item.fallback_used for item in calls) / count if count else 0.0),
        llm_latency_ms=sum(item.duration_ms for item in calls),
        total_tokens=sum(item.total_tokens for item in calls),
        estimated_cost_usd=round(sum(item.estimated_cost_usd for item in calls), 8),
        calls=calls,
    )


def _aggregate_variant(
    *,
    variant: str,
    shared_hash: str,
    suite: CapabilitySuite,
    trials: list[_VariantTrial],
) -> CapabilityVariantSummary:
    keys = list(trials[0].metrics)
    metrics = {key: mean(trial.metrics[key] for trial in trials) for key in keys}
    calls = latency = tokens = schema_valid_calls = total_llm_calls = 0
    cost = 0.0
    fallback_events = 0.0
    decision_history: dict[str, list[str]] = {case.id: [] for case in suite.cases}
    for trial in trials:
        op = _operational_metrics(trial.trace)
        trial_calls, trial_latency, trial_tokens, trial_cost, trial_fallback_rate, trial_schema = op
        calls += trial_calls
        total_llm_calls += trial_calls
        latency += trial_latency
        tokens += trial_tokens
        cost += trial_cost
        schema_valid_calls += trial_schema
        fallback_events += trial_fallback_rate * trial_calls
        by_id = {item.event_id: item for item in trial.assessments}
        for case_id in decision_history:
            decision_history[case_id].append(
                by_id[case_id].decision.value if case_id in by_id else "missing"
            )
    decision_consistency = mean(
        len(set(values)) == 1 for values in decision_history.values()
    )
    coverage_rate = mean(trial.coverage_valid for trial in trials)
    schema_rate = schema_valid_calls / total_llm_calls if total_llm_calls else 1.0
    avg_case_scores: list[CapabilityCaseScore] = []
    observed_outputs: list[CapabilityObservedOutput] = []
    if trials:
        avg_case_scores = trials[0].case_scores
        observed_outputs = [
            CapabilityObservedOutput(
                case_id=item.event_id,
                decision=item.decision,
                impact_score=item.impact_score,
                what_changed_zh=item.what_changed_zh,
                why_relevant_zh=item.why_relevant_zh,
                recommended_actions_zh=sanitize_user_facing_actions(
                    item.action_items_zh or item.action_items
                ),
            )
            for item in trials[0].assessments
        ]
    passed = (
        coverage_rate == 1.0
        and schema_rate == 1.0
        and _passes_thresholds(metrics, suite.thresholds)
    )
    return CapabilityVariantSummary(
        variant=variant,
        shared_context_hash=shared_hash,
        case_count=len(suite.cases),
        trials=len(trials),
        coverage_valid_rate=round(coverage_rate, 4),
        schema_valid_rate=round(schema_rate, 4),
        decision_acceptance=round(metrics["decision_acceptance"], 4),
        category_accuracy=round(metrics["category_accuracy"], 4),
        ndcg_at_5=round(metrics["ndcg_at_5"], 4),
        ndcg_at_10=round(metrics["ndcg_at_10"], 4),
        fact_coverage=round(metrics["fact_coverage"], 4),
        project_specificity=round(metrics["project_specificity"], 4),
        action_coverage=round(metrics["action_coverage"], 4),
        uncertainty_pass_rate=round(metrics["uncertainty_pass_rate"], 4),
        forbidden_claim_pass_rate=round(metrics["forbidden_claim_pass_rate"], 4),
        internal_leakage_pass_rate=round(metrics["internal_leakage_pass_rate"], 4),
        language_pass_rate=round(metrics["language_pass_rate"], 4),
        hard_negative_accuracy=round(metrics["hard_negative_accuracy"], 4),
        decision_consistency_rate=round(decision_consistency, 4),
        llm_call_count=calls,
        llm_latency_ms=latency,
        total_tokens=tokens,
        estimated_cost_usd=round(cost, 8),
        fallback_rate=round(fallback_events / calls if calls else 0.0, 4),
        passed=passed,
        case_scores=avg_case_scores,
        observed_outputs=observed_outputs,
        trial_summaries=[
            _trial_summary(trial, trial_index=index)
            for index, trial in enumerate(trials, start=1)
        ],
    )


def _batched_cases(suite: CapabilitySuite, batch_size: int) -> list[list[CapabilityCase]]:
    return [suite.cases[index : index + batch_size] for index in range(0, len(suite.cases), batch_size)]


def _subset_routes(routes: SupervisorOutput, event_ids: set[str]) -> SupervisorOutput:
    return SupervisorOutput(
        routes=[item for item in routes.routes if item.event_id in event_ids],
        batch_summary="Shared deterministic eval route for one fair mini-batch.",
    )


def _subset_evidence(
    evidence: ContextEvidenceOutput, event_ids: set[str]
) -> ContextEvidenceOutput:
    return ContextEvidenceOutput(
        results=[item for item in evidence.results if item.event_id in event_ids]
    )


async def _run_batched_variant(
    *,
    variant: Literal["single", "split"],
    provider: AgentProvider,
    mode: RunMode,
    suite: CapabilitySuite,
    project_profile: dict[str, Any],
    policy: dict[str, Any],
    routes: SupervisorOutput,
    evidence: ContextEvidenceOutput,
    batch_size: int,
    trial_index: int,
    trial_count: int,
    progress_callback: Callable[[dict[str, Any]], None] | None = None,
) -> tuple[list[SignalAssessment], list[TraceStep], bool, list[str]]:
    assessments: list[SignalAssessment] = []
    trace: list[TraceStep] = []
    coverage_valid = True
    coverage_diagnostics: list[str] = []
    batches = _batched_cases(suite, batch_size)
    for batch_index, batch_cases in enumerate(batches, start=1):
        batch_suite = suite.model_copy(update={"cases": batch_cases})
        batch_events = [case.event for case in batch_cases]
        event_ids = {case.id for case in batch_cases}
        batch_routes = _subset_routes(routes, event_ids)
        batch_evidence = _subset_evidence(evidence, event_ids)
        if variant == "single":
            items, steps, covered, diagnostics = await _run_single_trial(
                provider=provider,
                mode=mode,
                suite=batch_suite,
                project_profile=project_profile,
                policy=policy,
                events=batch_events,
                routes=batch_routes,
                evidence=batch_evidence,
            )
        else:
            items, steps, covered, diagnostics = await _run_split_trial(
                provider=provider,
                mode=mode,
                suite=batch_suite,
                project_profile=project_profile,
                policy=policy,
                events=batch_events,
                routes=batch_routes,
                evidence=batch_evidence,
            )
        assessments.extend(items)
        trace.extend(steps)
        coverage_valid = coverage_valid and covered
        coverage_diagnostics.extend(
            f"batch {batch_index}: {item}" for item in diagnostics
        )
        if progress_callback is not None:
            progress_callback(
                {
                    "stage": "batch_completed",
                    "variant": variant,
                    "trial_index": trial_index,
                    "trial_count": trial_count,
                    "batch_index": batch_index,
                    "batch_count": len(batches),
                    "batch_coverage_valid": covered,
                }
            )
    return assessments, trace, coverage_valid, coverage_diagnostics


def _experiment_signature(
    provider: AgentProvider, *, policy: dict[str, Any]
) -> str:
    profile = getattr(provider, "profile", None)
    if profile is not None and hasattr(profile, "model_dump"):
        profile_payload: object = profile.model_dump(mode="json")
    else:
        profile_payload = None
    payload = {
        "eval_version": CAPABILITY_EVAL_VERSION,
        "prompt_version": PROMPT_VERSION,
        "provider": str(getattr(provider, "provider", provider.name)),
        "model": provider.model,
        "model_profile": str(getattr(provider, "model_profile", "")),
        "profile": profile_payload,
        "policy": policy,
    }
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _checkpoint_path(
    checkpoint_dir: Path, *, variant: Literal["single", "split"], trial_index: int
) -> Path:
    return checkpoint_dir / f"{variant}.trial-{trial_index:02d}.json"


def _checkpoint_reusable(checkpoint: CapabilityTrialCheckpoint) -> bool:
    llm_steps = [step for step in checkpoint.trace if step.step == "llm_agent_call"]
    return (
        checkpoint.coverage_valid
        and bool(llm_steps)
        and all(step.schema_valid is True and not step.fallback_used for step in llm_steps)
    )


def _load_trial_checkpoint(
    path: Path,
    *,
    suite: CapabilitySuite,
    mode: RunMode,
    provider: str,
    model: str,
    shared_context_hash: str,
    experiment_signature: str,
    batch_size: int,
    variant: Literal["single", "split"],
    trial_index: int,
) -> _VariantTrial | None:
    if not path.exists():
        return None
    try:
        checkpoint = CapabilityTrialCheckpoint.model_validate_json(
            path.read_text(encoding="utf-8")
        )
    except Exception:
        return None
    if (
        checkpoint.suite != suite.suite
        or checkpoint.mode is not mode
        or checkpoint.provider != provider
        or checkpoint.model != model
        or checkpoint.shared_context_hash != shared_context_hash
        or checkpoint.experiment_signature != experiment_signature
        or checkpoint.batch_size != batch_size
        or checkpoint.variant != variant
        or checkpoint.trial_index != trial_index
        or not _checkpoint_reusable(checkpoint)
    ):
        return None
    case_scores, metrics = _trial_metrics(
        assessments=checkpoint.assessments, suite=suite
    )
    return _VariantTrial(
        assessments=checkpoint.assessments,
        trace=checkpoint.trace,
        coverage_valid=checkpoint.coverage_valid,
        coverage_diagnostics=checkpoint.coverage_diagnostics,
        case_scores=case_scores,
        metrics=metrics,
    )


def _write_trial_checkpoint(
    path: Path,
    *,
    suite: CapabilitySuite,
    mode: RunMode,
    provider: str,
    model: str,
    shared_context_hash: str,
    experiment_signature: str,
    batch_size: int,
    variant: Literal["single", "split"],
    trial_index: int,
    trial: _VariantTrial,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = CapabilityTrialCheckpoint(
        suite=suite.suite,
        mode=mode,
        provider=provider,
        model=model,
        shared_context_hash=shared_context_hash,
        experiment_signature=experiment_signature,
        batch_size=batch_size,
        variant=variant,
        trial_index=trial_index,
        assessments=trial.assessments,
        trace=trial.trace,
        coverage_valid=trial.coverage_valid,
        coverage_diagnostics=trial.coverage_diagnostics,
    )
    atomic_write_text(
        path, payload.model_dump_json(indent=2) + "\n"
    )


async def run_capability_comparison(
    *,
    provider: AgentProvider,
    mode: RunMode,
    suite: CapabilitySuite,
    project_profile: dict[str, Any],
    policy: dict[str, Any],
    trials: int = 1,
    batch_size: int = 8,
    checkpoint_dir: Path | None = None,
    resume: bool = True,
    progress_callback: Callable[[dict[str, Any]], None] | None = None,
) -> CapabilityComparisonSummary:
    """Compare single-Agent vs split semantics on equal mini-batches and evidence.

    The corpus-level 32-event one-call baseline is intentionally not used as the default:
    real GPT-5.6 experiments showed that its combined Impact+Action+Narrative response can
    exceed the production 60-75 second call budget. Both variants therefore receive the same
    deterministic mini-batches, isolating orchestration quality from response-size timeout.
    """

    if mode not in {RunMode.MOCK_AGENT, RunMode.AGENT}:
        raise ValueError("capability comparison requires mock-agent or agent mode")
    if trials < 1:
        raise ValueError("trials must be positive")
    if batch_size < 1 or batch_size > len(suite.cases):
        raise ValueError("batch_size must be between 1 and the capability case count")
    events = [case.event for case in suite.cases]
    routes = _routes_for(events, project_profile)
    evidence = _evidence_for(suite)
    shared_hash = _shared_context_hash(
        events=events,
        routes=routes,
        evidence=evidence,
        project_profile=project_profile,
    )
    single_trials: list[_VariantTrial] = []
    split_trials: list[_VariantTrial] = []
    provider_name = str(getattr(provider, "provider", provider.name))
    experiment_signature = _experiment_signature(provider, policy=policy)

    async def run_one(
        variant: Literal["single", "split"], *, trial_index: int
    ) -> _VariantTrial:
        checkpoint_path = (
            _checkpoint_path(
                checkpoint_dir, variant=variant, trial_index=trial_index
            )
            if checkpoint_dir is not None
            else None
        )
        if resume and checkpoint_path is not None:
            reused = _load_trial_checkpoint(
                checkpoint_path,
                suite=suite,
                mode=mode,
                provider=provider_name,
                model=provider.model,
                shared_context_hash=shared_hash,
                experiment_signature=experiment_signature,
                batch_size=batch_size,
                variant=variant,
                trial_index=trial_index,
            )
            if reused is not None:
                if progress_callback is not None:
                    progress_callback(
                        {
                            "stage": "trial_reused",
                            "variant": variant,
                            "trial_index": trial_index,
                            "trial_count": trials,
                        }
                    )
                return reused
        if progress_callback is not None:
            progress_callback(
                {
                    "stage": "trial_started",
                    "variant": variant,
                    "trial_index": trial_index,
                    "trial_count": trials,
                }
            )
        assessments, trace, coverage, coverage_diagnostics = await _run_batched_variant(
            variant=variant,
            provider=provider,
            mode=mode,
            suite=suite,
            project_profile=project_profile,
            policy=policy,
            routes=routes,
            evidence=evidence,
            batch_size=batch_size,
            trial_index=trial_index,
            trial_count=trials,
            progress_callback=progress_callback,
        )
        case_scores, metrics = _trial_metrics(assessments=assessments, suite=suite)
        trial = _VariantTrial(
            assessments=assessments,
            trace=trace,
            coverage_valid=coverage,
            coverage_diagnostics=coverage_diagnostics,
            case_scores=case_scores,
            metrics=metrics,
        )
        if checkpoint_path is not None:
            _write_trial_checkpoint(
                checkpoint_path,
                suite=suite,
                mode=mode,
                provider=provider_name,
                model=provider.model,
                shared_context_hash=shared_hash,
                experiment_signature=experiment_signature,
                batch_size=batch_size,
                variant=variant,
                trial_index=trial_index,
                trial=trial,
            )
        if progress_callback is not None:
            progress_callback(
                {
                    "stage": "trial_completed",
                    "variant": variant,
                    "trial_index": trial_index,
                    "trial_count": trials,
                    "coverage_valid": coverage,
                }
            )
        return trial

    # Alternate variant order across repeated trials to reduce temporal/provider-load bias.
    for zero_based_trial in range(trials):
        trial_index = zero_based_trial + 1
        if zero_based_trial % 2 == 0:
            single_trials.append(await run_one("single", trial_index=trial_index))
            split_trials.append(await run_one("split", trial_index=trial_index))
        else:
            split_trials.append(await run_one("split", trial_index=trial_index))
            single_trials.append(await run_one("single", trial_index=trial_index))

    variants = [
        _aggregate_variant(
            variant="shared-evidence-single-agent",
            shared_hash=shared_hash,
            suite=suite,
            trials=single_trials,
        ),
        _aggregate_variant(
            variant="split-impact-action-narrative",
            shared_hash=shared_hash,
            suite=suite,
            trials=split_trials,
        ),
    ]
    comparison_valid = all(
        variant.shared_context_hash == shared_hash
        and variant.coverage_valid_rate == 1.0
        and variant.schema_valid_rate == 1.0
        and variant.fallback_rate == 0.0
        for variant in variants
    )
    if mode is RunMode.MOCK_AGENT:
        state: RecommendationState = "plumbing_only"
        reason = (
            "Mock-agent results validate Eval plumbing only. They must not be used to choose "
            "the production architecture."
        )
    elif not comparison_valid:
        state = "invalid_provider_or_schema"
        reason = (
            "The real-provider comparison is invalid because at least one variant had incomplete "
            "coverage, schema-invalid output, or fallback execution. Do not compare quality, cost, "
            "or architecture from these fallback-derived metrics; fix the provider/schema failure "
            "and rerun the frozen suite."
        )
    elif trials < 3:
        state = "needs_repeated_real_trials"
        reason = (
            "Real-provider comparison is available, but fewer than three repeated trials are "
            "not enough to judge Agent stability."
        )
    else:
        state = "baseline_matrix_ready_no_auto_promotion"
        reason = (
            "Repeated real-provider metrics are ready for review. SignalHarness intentionally "
            "does not auto-promote an architecture until human-calibrated narrative judging and "
            "production episodes are also available."
        )
    return CapabilityComparisonSummary(
        suite=suite.suite,
        case_count=len(suite.cases),
        trials=trials,
        batch_size=batch_size,
        mode=mode,
        provider=provider_name,
        model=provider.model,
        eval_version=CAPABILITY_EVAL_VERSION,
        grader_version=CAPABILITY_GRADER_VERSION,
        scoring_version=GUARDED_SCORING_VERSION,
        presentation_version=PRESENTATION_VERSION,
        evaluation_source="provider_generation",
        prompt_version=PROMPT_VERSION,
        experiment_signature=experiment_signature,
        shared_context_hash=shared_hash,
        variants=variants,
        comparison_valid=comparison_valid,
        recommendation_state=state,
        recommendation_reason=reason,
    )


async def run_offline_trajectory_eval(
    *,
    root: Path,
    config_dir: Path,
    suite: CapabilitySuite,
    project_id: str = "signalharness",
) -> TrajectoryEvalSummary:
    """Run trajectory-contract cases one at a time through the full offline Harness.

    Per-case execution avoids false attribution from one batched Trace containing tools for
    unrelated source types. The mock Agent path is deliberate here: this Eval measures runtime
    behavior contracts, not real-provider intelligence.
    """

    from tempfile import TemporaryDirectory

    from signal_harness.runtime.workflow import SignalHarnessWorkflow

    selected = [
        case
        for case in suite.cases
        if any(
            (
                case.expected.trajectory.required_agents,
                case.expected.trajectory.forbidden_agents,
                case.expected.trajectory.required_tools,
                case.expected.trajectory.forbidden_tools,
            )
        )
        or case.expected.trajectory.max_tool_requests is not None
        or case.expected.trajectory.require_no_fallback
        or case.expected.trajectory.require_schema_valid
    ]
    failures: dict[str, list[str]] = {}
    work_root = root / "work"
    work_root.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(
        prefix="signalharness-trajectory-eval-", dir=work_root
    ) as temp:
        temp_root = Path(temp)
        for case in selected:
            case_root = temp_root / case.id
            case_root.mkdir(parents=True, exist_ok=True)
            fixture = case_root / "event.json"
            atomic_write_text(
                fixture,
                json.dumps(
                    [case.event.model_dump(mode="json")],
                    indent=2,
                    ensure_ascii=False,
                )
                + "\n",
            )
            workflow = SignalHarnessWorkflow(
                cwd=root,
                config_dir=config_dir,
                output_dir=case_root / "outputs",
                state_dir=case_root / "state",
                mode=RunMode.MOCK_AGENT,
                project_id=project_id,
            )
            result = await workflow.scan(
                fixture=fixture, max_events=1, interactive=False
            )
            one_case_suite = suite.model_copy(update={"cases": [case]})
            score = evaluate_trajectory_suite(
                trace=list(result.trace.steps), suite=one_case_suite
            )
            if score.failures:
                failures.update(score.failures)
    evaluated = len(selected)
    passed = evaluated - len(failures)
    return TrajectoryEvalSummary(
        evaluated_cases=evaluated,
        passed_cases=passed,
        pass_rate=(passed / evaluated if evaluated else 1.0),
        failures=failures,
    )


def write_trajectory_eval(
    output_dir: Path, summary: TrajectoryEvalSummary
) -> dict[str, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / "trajectory_eval_summary.json"
    md_path = output_dir / "trajectory_eval_summary.md"
    atomic_write_text(
        json_path,
        json.dumps(summary.model_dump(mode="json"), indent=2, ensure_ascii=False) + "\n",
    )
    lines = [
        "# SignalHarness Trajectory Eval",
        "",
        f"**Cases:** {summary.evaluated_cases}",
        f"**Passed:** {summary.passed_cases}",
        f"**Pass rate:** {summary.pass_rate:.3f}",
        "",
        "This Eval runs each behavior-contract case separately through the full offline mock Harness so tool/Agent attribution remains case-specific.",
        "",
    ]
    if summary.failures:
        lines.extend(["## Failures", ""])
        for case_id, failures in summary.failures.items():
            lines.append(f"- `{case_id}`: {', '.join(failures)}")
        lines.append("")
    atomic_write_text(md_path, "\n".join(lines))
    return {"json": json_path, "markdown": md_path}


def write_capability_comparison(
    output_dir: Path, summary: CapabilityComparisonSummary
) -> dict[str, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / "capability_eval_summary.json"
    md_path = output_dir / "capability_eval_summary.md"
    atomic_write_text(
        json_path,
        json.dumps(summary.model_dump(mode="json"), indent=2, ensure_ascii=False) + "\n",
    )
    lines = [
        "# SignalHarness Capability Eval",
        "",
        f"**Suite:** `{summary.suite}`",
        f"**Cases:** {summary.case_count}",
        f"**Trials:** {summary.trials}",
        f"**Fair mini-batch size:** {summary.batch_size}",
        f"**Provider/model:** {summary.provider} / {summary.model}",
        f"**Shared context:** `{summary.shared_context_hash}`",
        f"**Comparison valid:** {'yes' if summary.comparison_valid else 'no'}",
        f"**Recommendation state:** `{summary.recommendation_state}`",
        "",
        summary.recommendation_reason,
        "",
        "| Variant | Decision | nDCG@5 | nDCG@10 | Facts | Project | Actions | Uncertainty | Hard neg | Consistency | Calls | Tokens | Cost USD |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for item in summary.variants:
        lines.append(
            f"| {item.variant} | {item.decision_acceptance:.3f} | {item.ndcg_at_5:.3f} | "
            f"{item.ndcg_at_10:.3f} | {item.fact_coverage:.3f} | "
            f"{item.project_specificity:.3f} | {item.action_coverage:.3f} | "
            f"{item.uncertainty_pass_rate:.3f} | {item.hard_negative_accuracy:.3f} | "
            f"{item.decision_consistency_rate:.3f} | {item.llm_call_count} | "
            f"{item.total_tokens} | {item.estimated_cost_usd:.6f} |"
        )
    lines.extend(
        [
            "",
            "This suite is a capability dataset, not the 40-case regression protection set.",
            "The single-agent and split semantic variants receive the same Event, Project Profile, deterministic Route, curated Evidence packet, and mini-batch partition.",
            "",
        ]
    )
    atomic_write_text(md_path, "\n".join(lines))
    return {"json": json_path, "markdown": md_path}
