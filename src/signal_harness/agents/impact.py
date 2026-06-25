"""Project-specific impact analysis agent."""

from __future__ import annotations

from typing import Any

from signal_harness.agents.models import ImpactResult
from signal_harness.signal.schemas import ScoreBreakdown, SignalEvent


class ImpactAgent:
    """Map deterministic scores to affected project modules and reasoning."""

    name = "ImpactAgent"

    def run(
        self,
        event: SignalEvent,
        project_profile: dict[str, Any],
        breakdown: ScoreBreakdown,
    ) -> ImpactResult:
        text = f"{event.title} {event.content}".lower()
        modules = [
            str(module)
            for module in project_profile.get("critical_modules", [])
            if str(module).lower() in text
        ]
        dependencies = [
            str(item)
            for item in project_profile.get("dependencies", [])
            if str(item).lower() in f"{event.source_name} {text}".lower()
        ]
        affected = list(dict.fromkeys([*modules, *dependencies]))
        if not affected and breakdown.project_relevance_score >= 60:
            affected = ["project-wide"]
        reason = (
            f"Rule score {breakdown.final_score:.2f}/100; "
            f"project relevance {breakdown.project_relevance_score:.2f}/100."
        )
        if affected:
            reason += f" Likely affected: {', '.join(affected)}."
        return ImpactResult(
            relevance_score=breakdown.project_relevance_score,
            impact_score=breakdown.final_score,
            affected_modules=affected,
            reason=reason,
        )
