"""Concrete, non-mutating action planning agent."""

from __future__ import annotations

from signal_harness.agents.models import ActionResult, ImpactResult
from signal_harness.signal.schemas import SignalCategory, SignalDecision, SignalEvent


class ActionAgent:
    """Generate review tasks without editing code or opening pull requests."""

    name = "ActionAgent"

    def run(
        self,
        event: SignalEvent,
        category: SignalCategory,
        impact: ImpactResult,
        decision: SignalDecision,
    ) -> ActionResult:
        if decision is SignalDecision.IGNORE:
            return ActionResult(action_items=[])
        target = ", ".join(impact.affected_modules) or "the project"
        items = [f"Review the primary source and confirm the impact on {target}."]
        if category is SignalCategory.DEPENDENCY_UPDATE:
            items.append("Compare release or issue details with the currently pinned dependency.")
            items.append("Create a manual compatibility test plan before changing code.")
        elif category in {
            SignalCategory.CHECKPOINT_PERSISTENCE_SIGNAL,
            SignalCategory.STRUCTURED_OUTPUT_SIGNAL,
            SignalCategory.TOOL_CALLING_SIGNAL,
            SignalCategory.PROVIDER_COMPATIBILITY_SIGNAL,
        }:
            items.append("Reproduce the behavior against a small local smoke case before changing code.")
            items.append("Check whether schema validation, fallback, or permission guards need adjustment.")
        elif category is SignalCategory.SECURITY_SUPPLY_CHAIN:
            items.append("Review the advisory and confirm exposure before any dependency or config change.")
        elif category is SignalCategory.POLICY_SIGNAL:
            items.append("Identify compliance or licensing obligations and assign an owner.")
        elif category is SignalCategory.COMPETITOR_UPDATE:
            items.append("Record the product difference and decide whether roadmap review is needed.")
        elif category is SignalCategory.EXPERT_OPINION:
            items.append("Validate the recommendation against an official or primary source.")
        elif decision in {SignalDecision.ALERT, SignalDecision.ACTION_REQUIRED}:
            items.append("Assign a team member to validate the signal within one working day.")
        if event.url:
            items.append(f"Preserve the evidence link in the review ticket: {event.url}")
        return ActionResult(action_items=list(dict.fromkeys(items)))
