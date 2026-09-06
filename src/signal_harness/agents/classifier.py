"""Rule-grounded signal classification agent."""

from __future__ import annotations

import re
from typing import Any

from signal_harness.agents.models import ClassificationResult
from signal_harness.signal.text_semantics import any_affirmed_term
from signal_harness.signal.schemas import SignalCategory, SignalEvent

DEPENDENCY_IMPACT_TERMS = (
    "breaking",
    "security",
    "cve",
    "vulnerability",
    "deprecated",
    "deprecation",
    "supply chain",
    "compatibility",
    "regression",
    "migration",
    "api change",
    "api compatibility",
    "schema",
    "validation",
    "json schema compatibility",
)


def _term_in_text(text: str, term: str) -> bool:
    cleaned = term.strip().lower()
    if not cleaned:
        return False
    pattern = rf"(?<![a-z0-9]){re.escape(cleaned)}(?![a-z0-9])"
    return re.search(pattern, text) is not None




class ClassifierAgent:
    """Assign a business category while making noise decisions explicit."""

    name = "ClassifierAgent"

    def run(
        self,
        event: SignalEvent,
        project_profile: dict[str, Any],
    ) -> ClassificationResult:
        text = f"{event.source_name} {event.title} {event.content}".lower()
        content_text = f"{event.title} {event.content}".lower()
        ignore_terms = [
            str(value).lower() for value in project_profile.get("ignore_keywords", [])
        ]
        if any(term in text for term in ignore_terms):
            return ClassificationResult(
                category=SignalCategory.NOISE,
                reason="Matched a project ignore keyword.",
            )

        if (
            event.source_type == "github_issue"
            and any(
                value in content_text
                for value in ("policy", "permission", "regulation", "license", "compliance")
            )
        ):
            return ClassificationResult(
                category=SignalCategory.POLICY_SIGNAL,
                reason="The GitHub issue proposes a policy, permission, or compliance change.",
            )

        dependencies = [str(value).lower() for value in project_profile.get("dependencies", [])]
        competitors = [
            str(value).lower() for value in project_profile.get("competitors", [])
        ]
        direct_dependency = any(_term_in_text(text, value) for value in dependencies)
        dependency_impact = any_affirmed_term(text, DEPENDENCY_IMPACT_TERMS)
        if direct_dependency and dependency_impact:
            category = SignalCategory.DEPENDENCY_UPDATE
            reason = (
                "The signal names a tracked direct dependency and includes breaking, "
                "security, migration, schema, API compatibility, or regression impact."
            )
        elif any(value in text for value in ("cve", "vulnerability", "supply chain", "malware")):
            category = SignalCategory.SECURITY_SUPPLY_CHAIN
            reason = "The signal describes security or supply-chain risk."
        elif any(value in text for value in ("structured output", "json schema", "schema")):
            category = SignalCategory.STRUCTURED_OUTPUT_SIGNAL
            reason = "The signal affects structured output or schema compatibility."
        elif any(value in text for value in ("tool call", "tool_call", "tool calling", "wrapper", "interrupt")):
            category = SignalCategory.TOOL_CALLING_SIGNAL
            reason = "The signal affects tool-calling or tool wrapper behavior."
        elif any(value in text for value in ("checkpoint", "persistence", "durability")):
            category = SignalCategory.CHECKPOINT_PERSISTENCE_SIGNAL
            reason = "The signal affects checkpointing or persistence semantics."
        elif any(value in text for value in ("provider", "model api", "openai-compatible", "json mode")):
            category = SignalCategory.PROVIDER_COMPATIBILITY_SIGNAL
            reason = "The signal may affect provider or model API compatibility."
        elif any(value in text for value in ("benchmark", "evaluation", "eval", "leaderboard")):
            category = SignalCategory.EVALUATION_BENCHMARK_SIGNAL
            reason = "The signal is related to evaluation or benchmark behavior."
        elif any(
            value in content_text
            for value in ("rss parser", "feed parser", "source collection", "crawler")
        ):
            category = SignalCategory.SOURCE_COLLECTION_SIGNAL
            reason = "The signal affects source collection or feed reliability."
        elif (
            any(value in content_text for value in ("documentation", "docs", "readme"))
            and any(value in content_text for value in ("typo", "wording", "copy", "example"))
        ):
            category = SignalCategory.DOCS_CHANGE_SIGNAL
            reason = "The signal is a documentation-only change without runtime impact."
        elif direct_dependency and event.source_type == "github_release":
            category = SignalCategory.ECOSYSTEM_ISSUE
            reason = (
                "The signal is a tracked direct dependency release without confirmed "
                "breaking, security, migration, schema, API compatibility, or regression impact."
            )
        elif any(value in text for value in competitors):
            category = SignalCategory.COMPETITOR_UPDATE
            reason = "The signal names a tracked competitor."
        elif any(word in text for word in ("policy", "regulation", "license", "compliance")):
            category = SignalCategory.POLICY_SIGNAL
            reason = "The content contains policy or compliance language."
        elif event.source_type == "github_release":
            category = SignalCategory.ECOSYSTEM_ISSUE
            reason = "The signal is an ecosystem release without confirmed breaking/security impact."
        elif event.source_type == "rss":
            category = SignalCategory.EXPERT_OPINION
            reason = "The signal originates from a tracked technical or expert feed."
        elif event.source_type == "github_issue":
            category = SignalCategory.AGENT_RUNTIME_SIGNAL
            reason = "The GitHub issue is an ecosystem runtime signal rather than a dependency update."
        elif event.source_type == "team_update":
            category = SignalCategory.TEAM_UPDATE
            reason = "The signal is a project issue or team update."
        elif event.source_type == "web_change":
            category = SignalCategory.MARKET_SIGNAL
            reason = "The signal records an externally observed product or market change."
        else:
            category = SignalCategory.MARKET_SIGNAL
            reason = "The signal is relevant external project context."
        return ClassificationResult(category=category, reason=reason)
