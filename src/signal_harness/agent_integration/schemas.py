"""Strict schemas exchanged by the five LLM Agents."""

from __future__ import annotations

from typing import Any, Literal, TypeAlias, cast

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from signal_harness.signal.schemas import (
    PolicyUpdateProposal,
    SignalCategory,
    SourceQuality,
)


RequiredAgent: TypeAlias = Literal[
    "context_evidence",
    "impact",
    "action",
    "learning_observation",
]

RepairTarget: TypeAlias = Literal["context_evidence", "impact"]


class SupervisorRoute(BaseModel):
    model_config = ConfigDict(extra="forbid")

    event_id: str
    category: SignalCategory
    analyze: bool = True
    routing_reason: str
    required_agents: list[RequiredAgent] = Field(default_factory=list)
    skip_reason: str | None = None
    noise_reason: str | None = None
    related_cluster_id: str | None = None

    @field_validator("category", mode="before")
    @classmethod
    def _normalize_category_alias(cls, value: Any) -> Any:
        """Accept conservative LLM category aliases without weakening schema safety."""

        if isinstance(value, SignalCategory):
            return value
        if not isinstance(value, str):
            return value
        normalized = value.strip().lower().replace("-", "_").replace(" ", "_")
        aliases = {
            "dependency": SignalCategory.DEPENDENCY_UPDATE,
            "dependencies": SignalCategory.DEPENDENCY_UPDATE,
            "release": SignalCategory.DEPENDENCY_UPDATE,
            "ecosystem": SignalCategory.ECOSYSTEM_ISSUE,
            "ecosystem_signal": SignalCategory.ECOSYSTEM_ISSUE,
            "runtime": SignalCategory.AGENT_RUNTIME_SIGNAL,
            "agent_runtime": SignalCategory.AGENT_RUNTIME_SIGNAL,
            "agent_runtime_issue": SignalCategory.AGENT_RUNTIME_SIGNAL,
            "checkpoint": SignalCategory.CHECKPOINT_PERSISTENCE_SIGNAL,
            "checkpoint_persistence": SignalCategory.CHECKPOINT_PERSISTENCE_SIGNAL,
            "persistence": SignalCategory.CHECKPOINT_PERSISTENCE_SIGNAL,
            "schema": SignalCategory.STRUCTURED_OUTPUT_SIGNAL,
            "json_schema": SignalCategory.STRUCTURED_OUTPUT_SIGNAL,
            "structured_output": SignalCategory.STRUCTURED_OUTPUT_SIGNAL,
            "structured_outputs": SignalCategory.STRUCTURED_OUTPUT_SIGNAL,
            "tool": SignalCategory.TOOL_CALLING_SIGNAL,
            "tools": SignalCategory.TOOL_CALLING_SIGNAL,
            "tool_calling": SignalCategory.TOOL_CALLING_SIGNAL,
            "tool_call": SignalCategory.TOOL_CALLING_SIGNAL,
            "provider": SignalCategory.PROVIDER_COMPATIBILITY_SIGNAL,
            "provider_api": SignalCategory.PROVIDER_COMPATIBILITY_SIGNAL,
            "provider_compatibility": SignalCategory.PROVIDER_COMPATIBILITY_SIGNAL,
            "model_api": SignalCategory.PROVIDER_COMPATIBILITY_SIGNAL,
            "source": SignalCategory.SOURCE_COLLECTION_SIGNAL,
            "source_collection": SignalCategory.SOURCE_COLLECTION_SIGNAL,
            "collection": SignalCategory.SOURCE_COLLECTION_SIGNAL,
            "rss": SignalCategory.SOURCE_COLLECTION_SIGNAL,
            "security": SignalCategory.SECURITY_SUPPLY_CHAIN,
            "supply_chain": SignalCategory.SECURITY_SUPPLY_CHAIN,
            "supply_chain_security": SignalCategory.SECURITY_SUPPLY_CHAIN,
            "security_supply_chain_signal": SignalCategory.SECURITY_SUPPLY_CHAIN,
            "evaluation": SignalCategory.EVALUATION_BENCHMARK_SIGNAL,
            "eval": SignalCategory.EVALUATION_BENCHMARK_SIGNAL,
            "benchmark": SignalCategory.EVALUATION_BENCHMARK_SIGNAL,
            "benchmarks": SignalCategory.EVALUATION_BENCHMARK_SIGNAL,
            "docs": SignalCategory.DOCS_CHANGE_SIGNAL,
            "documentation": SignalCategory.DOCS_CHANGE_SIGNAL,
            "docs_change": SignalCategory.DOCS_CHANGE_SIGNAL,
            "changelog": SignalCategory.DOCS_CHANGE_SIGNAL,
            "competitor": SignalCategory.COMPETITOR_UPDATE,
            "market": SignalCategory.MARKET_SIGNAL,
            "policy": SignalCategory.POLICY_SIGNAL,
            "expert": SignalCategory.EXPERT_OPINION,
            "expert_insight": SignalCategory.EXPERT_OPINION,
            "expert_opinion_signal": SignalCategory.EXPERT_OPINION,
            "team": SignalCategory.TEAM_UPDATE,
        }
        return aliases.get(normalized, value)

    @model_validator(mode="after")
    def _validate_route_contract(self) -> "SupervisorRoute":
        if not self.analyze and self.required_agents:
            raise ValueError("non-analyzed routes cannot require downstream Agents")
        if self.noise_reason and self.analyze and "override" not in self.routing_reason.lower():
            raise ValueError(
                "analyzing a noise candidate requires an explicit override explanation"
            )
        return self


class SupervisorOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    routes: list[SupervisorRoute]
    batch_summary: str = ""


class ToolRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tool_name: str
    arguments: dict[str, Any] = Field(default_factory=dict)
    reason: str


class ToolObservation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tool_name: str
    status: Literal["success", "error", "blocked"]
    output_summary: str
    raw_output_ref: str | None = None
    error: str | None = None


class EvidenceToolPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_types_observed: list[str] = Field(default_factory=list)
    tool_requests: list[ToolRequest] = Field(default_factory=list)
    planning_summary: str = ""


class ContextEvidenceItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    event_id: str
    evidence_urls: list[str] = Field(default_factory=list)
    context_summary: str
    confidence: float = Field(ge=0, le=1)
    source_quality: SourceQuality
    unsupported_claims: list[str] = Field(default_factory=list)
    uncertainty: str = ""
    source_types_observed: list[str] = Field(default_factory=list)
    tools_requested: list[str] = Field(default_factory=list)
    tools_executed: list[str] = Field(default_factory=list)
    tool_errors: list[str] = Field(default_factory=list)


class ContextEvidenceOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    results: list[ContextEvidenceItem]


class RepairRequest(BaseModel):
    """Bounded repair suggestion emitted by downstream Agents.

    This is intentionally not a handoff-as-tool contract. Agents can only
    suggest two repair targets; Python decides whether a bounded repair pass is
    allowed by run limits, event caps, and permission/tool budgets.
    """

    model_config = ConfigDict(extra="forbid")

    target_agent: RepairTarget
    event_ids: list[str] = Field(min_length=1)
    reason: str = Field(min_length=1)
    severity: Literal["low", "medium", "high"] = "medium"

    @field_validator("event_ids")
    @classmethod
    def _event_ids_must_be_nonempty(cls, value: list[str]) -> list[str]:
        normalized = [item.strip() for item in value if item.strip()]
        if not normalized:
            raise ValueError("repair requests must name at least one event_id")
        return list(dict.fromkeys(normalized))


class ImpactItem(BaseModel):
    """No final_score field by design; extra score fields fail validation."""

    model_config = ConfigDict(extra="forbid")

    event_id: str
    affected_modules: list[str] = Field(default_factory=list)
    semantic_relevance: float = Field(ge=0, le=100)
    risk_level: Literal["low", "medium", "high", "critical"]
    impact_reason: str
    related_event_ids: list[str] = Field(default_factory=list)
    cross_source_confidence: float = Field(default=0, ge=0, le=1)
    conflicting_evidence: list[str] = Field(default_factory=list)


class ImpactOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    results: list[ImpactItem]
    repair_requests: list[RepairRequest] = Field(default_factory=list)


class ActionItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    event_id: str
    action_items: list[str] = Field(default_factory=list)
    critic_notes: str = ""
    approval_required: bool = False
    requested_actions: list[str] = Field(default_factory=list)


class ActionOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    results: list[ActionItem]
    repair_requests: list[RepairRequest] = Field(default_factory=list)


class ReplayEvaluation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    old_precision_proxy: float = Field(ge=0, le=1)
    new_precision_proxy: float = Field(ge=0, le=1)
    false_positive_reduction: int
    missed_signal_reduction: int
    recommendation: str


class LearningPolicyOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    policy_update_proposal: PolicyUpdateProposal
    skill_update_proposal: str
    watchlist_update_proposal: dict[str, Any]
    learning_summary: str
    memory_sections_read: list[str] = Field(default_factory=list)

    @field_validator("watchlist_update_proposal", mode="before")
    @classmethod
    def _watchlist_proposal_requires_approval(
        cls,
        value: Any,
    ) -> dict[str, Any]:
        if not isinstance(value, dict):
            raise ValueError("watchlist proposals must be objects")
        proposal = cast(dict[str, Any], dict(value))
        proposal.setdefault("requires_approval", True)
        if proposal.get("requires_approval") is not True:
            raise ValueError("watchlist proposals must require approval")
        return proposal
