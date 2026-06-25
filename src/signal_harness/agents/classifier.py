"""Rule-grounded signal classification agent."""

from __future__ import annotations

from typing import Any

from signal_harness.agents.models import ClassificationResult
from signal_harness.signal.schemas import SignalCategory, SignalEvent


class ClassifierAgent:
    """Assign a business category while making noise decisions explicit."""

    name = "ClassifierAgent"

    def run(
        self,
        event: SignalEvent,
        project_profile: dict[str, Any],
    ) -> ClassificationResult:
        text = f"{event.source_name} {event.title} {event.content}".lower()
        ignore_terms = [
            str(value).lower() for value in project_profile.get("ignore_keywords", [])
        ]
        if any(term in text for term in ignore_terms):
            return ClassificationResult(
                category=SignalCategory.NOISE,
                reason="Matched a project ignore keyword.",
            )

        dependencies = [
            str(value).lower() for value in project_profile.get("dependencies", [])
        ]
        competitors = [
            str(value).lower() for value in project_profile.get("competitors", [])
        ]
        if any(value in text for value in dependencies):
            category = SignalCategory.DEPENDENCY_UPDATE
            reason = "The source or content matches a tracked project dependency."
        elif any(value in text for value in competitors):
            category = SignalCategory.COMPETITOR_UPDATE
            reason = "The signal names a tracked competitor."
        elif event.source_type == "github_release":
            category = SignalCategory.DEPENDENCY_UPDATE
            reason = "The signal is an official software release."
        elif any(word in text for word in ("policy", "regulation", "license", "compliance")):
            category = SignalCategory.POLICY_SIGNAL
            reason = "The content contains policy or compliance language."
        elif event.source_type == "rss":
            category = SignalCategory.EXPERT_OPINION
            reason = "The signal originates from a tracked technical or expert feed."
        elif event.source_type in {"github_issue", "team_update"}:
            category = SignalCategory.TEAM_UPDATE
            reason = "The signal is a project issue or team update."
        elif event.source_type == "web_change":
            category = SignalCategory.MARKET_SIGNAL
            reason = "The signal records an externally observed product or market change."
        else:
            category = SignalCategory.MARKET_SIGNAL
            reason = "The signal is relevant external project context."
        return ClassificationResult(category=category, reason=reason)
