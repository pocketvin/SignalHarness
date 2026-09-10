"""Cost-aware routing for ChangeInsight production; no model decision is used here."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Literal

from signal_harness.intelligence.contracts import ChangeDigest
from signal_harness.intelligence.fact_capsule import FactCapsule, capsule_semantic_payload

Route = Literal["cache", "deterministic", "semantic"]


@dataclass(frozen=True)
class SemanticWorkItem:
    digest: ChangeDigest
    capsule: FactCapsule


@dataclass(frozen=True)
class RouteDecision:
    route: Route
    reason: str


def route_capsule(capsule: FactCapsule) -> RouteDecision:
    if capsule.deterministic_eligible:
        return RouteDecision("deterministic", "structured_fact_and_exact_project_relation")
    return RouteDecision("semantic", "semantic_fact_or_project_relation_needed")


def tiny_fast_path_candidate(
    items: list[SemanticWorkItem], *, max_changes: int, max_input_bytes: int
) -> bool:
    """Decision point only; runtime activation waits for a bounded real-model eval."""
    if not items or len(items) > max_changes:
        return False
    size = sum(
        len(
            json.dumps(
                capsule_semantic_payload(item.capsule, item.digest),
                ensure_ascii=False,
                separators=(",", ":"),
            ).encode()
        )
        for item in items
    )
    return size <= max_input_bytes
