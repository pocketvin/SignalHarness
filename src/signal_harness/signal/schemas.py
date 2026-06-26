"""Structured domain objects exchanged by SignalHarness agents and tools."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class SignalCategory(str, Enum):
    """Supported business categories for normalized project signals."""

    DEPENDENCY_UPDATE = "dependency_update"
    COMPETITOR_UPDATE = "competitor_update"
    MARKET_SIGNAL = "market_signal"
    POLICY_SIGNAL = "policy_signal"
    EXPERT_OPINION = "expert_opinion"
    TEAM_UPDATE = "team_update"
    NOISE = "noise"


class SignalDecision(str, Enum):
    """Action level assigned to one signal assessment."""

    IGNORE = "ignore"
    SAVE = "save"
    ALERT = "alert"
    ACTION_REQUIRED = "action_required"


class FeedbackLabel(str, Enum):
    """User feedback labels that can calibrate future scoring."""

    USEFUL = "useful"
    NOT_USEFUL = "not_useful"
    FALSE_POSITIVE = "false_positive"
    MISSED_SIGNAL = "missed_signal"
    TOO_GENERIC = "too_generic"


class SourceQuality(str, Enum):
    """Evidence quality assigned by the evidence agent."""

    OFFICIAL = "official"
    SECONDARY = "secondary"
    COMMUNITY = "community"
    UNVERIFIED = "unverified"


class ScoreBreakdown(BaseModel):
    """Debuggable deterministic components used to calculate a final score."""

    model_config = ConfigDict(extra="forbid")

    source_weight: float = Field(ge=0, le=100)
    keyword_match_score: float = Field(ge=0, le=100)
    project_relevance_score: float = Field(ge=0, le=100)
    novelty_score: float = Field(ge=0, le=100)
    urgency_score: float = Field(ge=0, le=100)
    feedback_adjustment: float = Field(ge=0, le=100)
    category_weight: float = Field(ge=0, le=1)
    final_score: float = Field(ge=0, le=100)


class AgentScoreBreakdown(BaseModel):
    """Guarded blend of deterministic and schema-validated LLM signals."""

    model_config = ConfigDict(extra="forbid")

    deterministic_base_score: float = Field(ge=0, le=100)
    semantic_relevance: float = Field(ge=0, le=100)
    evidence_confidence_score: float = Field(ge=0, le=100)
    deterministic_weight: float = Field(ge=0, le=1)
    semantic_weight: float = Field(ge=0, le=1)
    evidence_weight: float = Field(ge=0, le=1)
    policy_multiplier: float = Field(ge=0, le=1)
    final_score: float = Field(ge=0, le=100)


class SignalEvent(BaseModel):
    """Source-independent event produced by the normalization stage."""

    model_config = ConfigDict(extra="forbid")

    event_id: str = Field(min_length=1)
    source_type: str = Field(min_length=1)
    source_name: str = Field(min_length=1)
    title: str = Field(min_length=1)
    content: str = ""
    url: str = ""
    published_at: datetime | None = None
    raw_payload: dict[str, Any] = Field(default_factory=dict)
    collected_at: datetime

    @field_validator("event_id", "source_type", "source_name", "title")
    @classmethod
    def _strip_required_text(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("must not be blank")
        return normalized


class NoiseAssessment(BaseModel):
    """Pre-LLM noise hint that may lower priority but never deletes an event."""

    model_config = ConfigDict(extra="forbid")

    event_id: str
    is_noise_candidate: bool = False
    score_multiplier: float = Field(default=1.0, ge=0.1, le=1.0)
    noise_reason: str | None = None
    matched_rules: list[str] = Field(default_factory=list)


class SignalCluster(BaseModel):
    """Lightweight multi-source relationship group."""

    model_config = ConfigDict(extra="forbid")

    cluster_id: str
    topic: str
    related_event_ids: list[str]
    entities: list[str] = Field(default_factory=list)
    time_window: str
    source_types: list[str] = Field(default_factory=list)
    confidence: float = Field(ge=0, le=1)


class SourceTask(BaseModel):
    """Observable lifecycle record for one asynchronous source fetch."""

    model_config = ConfigDict(extra="forbid")

    task_id: str
    source_name: str
    source_type: str
    status: Literal[
        "pending",
        "running",
        "success",
        "partial_failure",
        "failed",
        "skipped",
    ]
    started_at: datetime | None = None
    finished_at: datetime | None = None
    duration_ms: int = Field(default=0, ge=0)
    error: str | None = None
    output_count: int = Field(default=0, ge=0)
    cache_hit: bool = False


class SignalAssessment(BaseModel):
    """Structured output jointly produced by the business agents."""

    model_config = ConfigDict(extra="forbid")

    event_id: str = Field(min_length=1)
    category: SignalCategory
    relevance_score: float = Field(ge=0, le=100)
    impact_score: float = Field(ge=0, le=100)
    confidence: float = Field(ge=0, le=1)
    affected_modules: list[str] = Field(default_factory=list)
    evidence_urls: list[str] = Field(default_factory=list)
    source_quality: SourceQuality = SourceQuality.UNVERIFIED
    reason: str = ""
    action_items: list[str] = Field(default_factory=list)
    decision: SignalDecision
    score_breakdown: ScoreBreakdown | None = None
    agent_score_breakdown: AgentScoreBreakdown | None = None
    related_event_ids: list[str] = Field(default_factory=list)
    cross_source_confidence: float = Field(default=0, ge=0, le=1)
    conflicting_evidence: list[str] = Field(default_factory=list)
    related_cluster_id: str | None = None
    noise_reason: str | None = None
    required_agents: list[str] = Field(default_factory=list)


class FeedbackRecord(BaseModel):
    """Immutable user judgment attached to a signal."""

    model_config = ConfigDict(extra="forbid")

    event_id: str = Field(min_length=1)
    feedback: FeedbackLabel
    note: str = ""
    created_at: datetime


class PolicyUpdateProposal(BaseModel):
    """Reviewable policy change proposal that is never auto-applied."""

    model_config = ConfigDict(extra="forbid")

    proposal_id: str = Field(min_length=1)
    reason: str = Field(min_length=1)
    old_policy: dict[str, Any]
    new_policy: dict[str, Any]
    changed_keywords: list[str] = Field(default_factory=list)
    changed_sources: list[str] = Field(default_factory=list)
    expected_effect: str = ""
    requires_approval: bool = True

    @field_validator("requires_approval")
    @classmethod
    def _approval_is_mandatory(cls, value: bool) -> bool:
        if not value:
            raise ValueError("SignalHarness policy proposals must require approval")
        return value


class TraceStep(BaseModel):
    """One observable workflow step emitted during a SignalHarness run."""

    model_config = ConfigDict(extra="forbid")

    step: str = Field(min_length=1)
    status: str = Field(pattern="^(success|error|skipped)$")
    agent: str | None = None
    input_count: int | None = Field(default=None, ge=0)
    output_count: int | None = Field(default=None, ge=0)
    duration_ms: int = Field(ge=0)
    detail: str = ""
    failed_sources: list[str] = Field(default_factory=list)
    agent_name: str | None = None
    mode: str | None = None
    model: str | None = None
    prompt_version: str | None = None
    input_event_id: str | None = None
    output_schema: str | None = None
    schema_valid: bool | None = None
    fallback_used: bool = False
    source_types_observed: list[str] = Field(default_factory=list)
    tools_requested: list[str] = Field(default_factory=list)
    tools_executed: list[str] = Field(default_factory=list)
    tool_errors: list[str] = Field(default_factory=list)
    blocked_tools: list[str] = Field(default_factory=list)
    event_input_count: int | None = Field(default=None, ge=0)
    tool_observation_count: int | None = Field(default=None, ge=0)
    source_type_count: int | None = Field(default=None, ge=0)
    tools_requested_count: int | None = Field(default=None, ge=0)
    tools_executed_count: int | None = Field(default=None, ge=0)
    exit_condition: str | None = None
    permission_checks: list[str] = Field(default_factory=list)
    prompt_prefix_hash: str | None = None
    static_context_hash: str | None = None
    dynamic_context_hash: str | None = None
    context_packet_version: str | None = None
    cache_strategy: str | None = None
    cache_hit: bool | None = None
    cache_events: list[str] = Field(default_factory=list)
    source_tasks: list[SourceTask] = Field(default_factory=list)
    error: str | None = None
