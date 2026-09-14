"""Versioned product contracts. No provider, framework, or ranking concepts in the UI."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

INTELLIGENCE_VERSION = "environment-v1.7"
CHANGE_INSIGHT_VERSION = "environment-shallow-v1.8"
LEGACY_CHANGE_INSIGHT_VERSIONS = ("environment-shallow-v1.7", "environment-shallow-v1.6", "environment-v1.3")
SYNTHESIS_VERSION = "environment-synthesis-v1.11"
DEEP_DIVE_VERSION = "environment-v1.3"  # deep-dive cache unchanged by report-only evolution


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid")


CorpusRole = Literal["external_environment", "project_activity"]
EvidencePosture = Literal["reported_issue", "mixed", "observed_change"]


class Evidence(Contract):
    evidence_id: str
    event_revision_id: int
    source_name: str
    source_type: str
    url: str
    authority: str
    excerpt: str
    excerpt_truncated: bool = False
    project_owned: bool = False


class ChangeDigest(Contract):
    change_id: str
    revision_id: str
    title: str
    entity: str
    kind: str
    published_at: str | None
    current_version: str | None = None
    corpus_role: CorpusRole = "external_environment"
    discovery_origin: Literal["watched", "discovered"] = "watched"
    discovery_basis: str = Field(default="", max_length=320)
    interpretation_hint: Literal["default", "routine_issue"] = "default"
    evidence: list[Evidence]


class ShallowInsight(Contract):
    change_id: str
    summary: str = Field(min_length=1, max_length=220)
    what_changed: str = Field(min_length=1, max_length=700)
    project_relation: Literal["direct", "context", "none", "unknown"]
    relation_reason: str = Field(max_length=400)
    attention: Literal["watch", "normal", "low"]
    topics: list[str] = Field(default_factory=list, max_length=5)
    evidence_ids: list[str] = Field(default_factory=list, max_length=16)
    uncertainty: str = Field(default="", max_length=400)


class ShallowModelRow(Contract):
    """Compact provider wire schema; converted to user-facing ShallowInsight in Python."""

    id: str
    s: str = Field(min_length=1, max_length=96)
    f: str = Field(min_length=1, max_length=240)
    r: Literal["direct", "context", "none", "unknown"]
    b: str = Field(default="", max_length=8)
    n: str = Field(default="", max_length=72)
    a: Literal["watch", "normal", "low"]
    t: list[str] = Field(default_factory=list, max_length=3)
    e: list[str] = Field(default_factory=list, max_length=8)
    u: str = Field(default="", max_length=100)


class ShallowModelBatch(Contract):
    x: list[ShallowModelRow]


class InsightBatch(Contract):
    """Legacy/cache-friendly public shallow contract; not the provider wire schema."""

    results: list[ShallowInsight]


class GroundedClaim(Contract):
    text: str = Field(min_length=1, max_length=360)
    supporting_change_ids: list[str] = Field(min_length=1, max_length=100)


class DirectionCandidate(Contract):
    topic_key: str = Field(min_length=1, max_length=100)
    previous_direction_id: str | None = None
    title: str = Field(min_length=1, max_length=56)
    explanation: str = Field(min_length=1, max_length=520)
    supporting_change_ids: list[str] = Field(min_length=2, max_length=200)
    contradicting_change_ids: list[str] = Field(default_factory=list, max_length=100)
    project_connection: str = Field(default="", max_length=240)
    watch_next: list[str] = Field(default_factory=list, max_length=2)
    uncertainty: str = Field(default="", max_length=320)


class RadarItem(Contract):
    radar_type: Literal["new_solution", "emerging_direction"]
    title: str = Field(min_length=1, max_length=64)
    explanation: str = Field(min_length=1, max_length=480)
    supporting_change_ids: list[str] = Field(min_length=1, max_length=40)
    project_connection: str = Field(default="", max_length=240)
    why_now: str = Field(default="", max_length=240)
    uncertainty: str = Field(default="", max_length=280)


class SynthesisOutput(Contract):
    """One strong-model call produces directions, radar, AND report; no narrative agent."""

    brief: list[GroundedClaim] = Field(default_factory=list, max_length=5)
    directions: list[DirectionCandidate] = Field(default_factory=list, max_length=6)
    featured_change_ids: list[str] = Field(default_factory=list, max_length=5)
    radar: list[RadarItem] = Field(default_factory=list, max_length=3)


class Direction(Contract):
    direction_id: str
    revision_id: str
    topic_key: str
    title: str
    explanation: str
    state: Literal["new", "continuing", "strengthening", "weakening", "uncertain"]
    state_reason: str
    supporting_change_ids: list[str]
    contradicting_change_ids: list[str]
    independent_source_count: int
    authoritative_source_count: int = 0
    evidence_posture: EvidencePosture = "observed_change"
    previous_support_count: int | None = None
    project_connection: str
    watch_next: list[str]
    uncertainty: str


class ProductChange(Contract):
    change_id: str
    revision_id: str
    title: str
    entity: str
    kind: str
    published_at: str | None
    corpus_role: CorpusRole = "external_environment"
    discovery_origin: Literal["watched", "discovered"] = "watched"
    discovery_basis: str = ""
    summary: str
    what_changed: str
    project_relation: Literal["direct", "context", "none", "unknown"]
    relation_reason: str
    attention: Literal["watch", "normal", "low"]
    topics: list[str]
    uncertainty: str
    interpretation_status: Literal["ready", "unavailable"]
    relevant: bool
    featured: bool = False
    evidence: list[Evidence]


class EnvironmentReport(Contract):
    data_origin: Literal["live", "replay"] = "live"
    contract_version: str = INTELLIGENCE_VERSION
    report_id: str
    scan_id: str
    project_id: str
    profile_revision_id: str
    created_at: str
    window: dict[str, Any]
    status: Literal["complete", "degraded"]
    coverage_status: str
    counts: dict[str, int]
    brief: list[GroundedClaim]
    directions: list[Direction]
    radar: list[RadarItem] = Field(default_factory=list)
    risks: list[GroundedClaim]
    opportunities: list[GroundedClaim]
    featured: list[ProductChange]
    notices: list[str]
    sources: list[dict[str, Any]]


class UsageReference(Contract):
    reference_id: str
    path: str
    line: int
    excerpt: str
    kind: str = "text_reference"


class DeepDiveOutput(Contract):
    summary: str = Field(min_length=1, max_length=1800)
    impact: str = Field(min_length=1, max_length=2200)
    evidence_ids: list[str] = Field(default_factory=list, max_length=30)
    usage_reference_ids: list[str] = Field(default_factory=list, max_length=30)
    verification_steps: list[str] = Field(default_factory=list, max_length=6)
    uncertainty: str = Field(default="", max_length=1200)


class Progress(Contract):
    stage: str
    message: str
    completed: int = Field(default=0, ge=0)
    total: int | None = Field(default=None, ge=0)
    status: Literal["running", "complete", "degraded", "error", "cancelled"] = "running"
