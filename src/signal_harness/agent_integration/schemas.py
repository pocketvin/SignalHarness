"""Strict schemas exchanged by the five LLM Agents."""

from __future__ import annotations

from typing import Any, Literal, TypeAlias

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

    @field_validator("watchlist_update_proposal")
    @classmethod
    def _watchlist_proposal_requires_approval(
        cls,
        value: dict[str, Any],
    ) -> dict[str, Any]:
        if value.get("requires_approval") is not True:
            raise ValueError("watchlist proposals must require approval")
        return value
