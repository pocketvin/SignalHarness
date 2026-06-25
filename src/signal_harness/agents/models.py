"""Structured intermediate outputs exchanged by SignalHarness agents."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from signal_harness.signal.schemas import SignalCategory, SourceQuality


class ClassificationResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    category: SignalCategory
    reason: str


class EvidenceResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    evidence_urls: list[str] = Field(default_factory=list)
    source_quality: SourceQuality
    confidence: float = Field(ge=0, le=1)
    reason: str


class ImpactResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    relevance_score: float = Field(ge=0, le=100)
    impact_score: float = Field(ge=0, le=100)
    affected_modules: list[str] = Field(default_factory=list)
    reason: str


class ActionResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action_items: list[str] = Field(default_factory=list)
