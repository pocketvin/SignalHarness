"""Deterministic risk classification for staged learning proposals."""

from __future__ import annotations

import json
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from signal_harness.agent_integration.schemas import LearningPolicyOutput, ReplayEvaluation

ProposalRiskLevel = Literal["low", "medium", "high", "critical"]


class ProposalRiskReport(BaseModel):
    """Reviewable risk signal for one LearningPolicyAgent proposal bundle."""

    model_config = ConfigDict(extra="forbid")

    risk_level: ProposalRiskLevel
    reasons: list[str] = Field(default_factory=list)
    auto_stage_allowed: bool
    apply_requires_approval: bool
    replay_gate_passed: bool


class ProposalRiskClassifier:
    """Classify proposal risk without model calls or external services."""

    def classify(
        self,
        learning: LearningPolicyOutput,
        replay: ReplayEvaluation | dict[str, Any] | None,
    ) -> ProposalRiskReport:
        reasons: list[str] = []
        risk: ProposalRiskLevel = "low"
        proposal = learning.policy_update_proposal

        risk = self._raise_if(
            risk,
            "medium",
            bool(proposal.changed_keywords or proposal.changed_sources),
        )
        if proposal.changed_keywords:
            reasons.append("changed_keywords are reviewable low/medium-risk tuning.")
        if proposal.changed_sources:
            reasons.append("changed_sources require review before source weighting changes.")

        old_policy = proposal.old_policy
        new_policy = proposal.new_policy
        if old_policy.get("thresholds") != new_policy.get("thresholds"):
            risk = self._raise_if(risk, "high", True)
            reasons.append("alert/action thresholds changed.")
        if old_policy.get("permission_policy") != new_policy.get("permission_policy"):
            risk = self._raise_if(risk, "high", True)
            reasons.append("permission_policy changed.")
        if old_policy.get("enabled_tools") != new_policy.get("enabled_tools"):
            risk = self._raise_if(risk, "high", True)
            reasons.append("enabled tool permissions changed.")
        if old_policy.get("disabled_tools") != new_policy.get("disabled_tools"):
            risk = self._raise_if(risk, "high", True)
            reasons.append("disabled tool permissions changed.")

        category_reason = self._category_weight_reason(old_policy, new_policy)
        if category_reason:
            risk = self._raise_if(risk, "high", True)
            reasons.append(category_reason)

        high_risk_text = json.dumps(
            {
                "watchlist_update_proposal": learning.watchlist_update_proposal,
                "skill_update_proposal": learning.skill_update_proposal,
            },
            ensure_ascii=False,
            sort_keys=True,
        ).lower()
        high_patterns = {
            "delete watchlist source": "deleting watchlist sources is high risk.",
            "remove_watchlist_source": "removing watchlist sources is high risk.",
            "external notification": "external notifications are high risk.",
            "send_team_notification": "external notifications are high risk.",
            "create_github_issue": "creating GitHub issues is high risk.",
            "project_profile": "project profile changes are high risk.",
            "permission_policy": "permission policy changes are high risk.",
            "tool permission": "tool permission changes are high risk.",
            "enabled_tools": "tool permission changes are high risk.",
        }
        for pattern, reason in high_patterns.items():
            if pattern in high_risk_text:
                risk = self._raise_if(risk, "high", True)
                reasons.append(reason)

        if not reasons:
            reasons.append(
                "Proposal appears limited to review notes, useful signal patterns, "
                "false-positive notes, or source reliability notes."
            )

        replay_gate_passed, replay_reason = self._replay_gate(replay)
        reasons.append(replay_reason)
        auto_stage_allowed = risk != "critical"
        apply_requires_approval = risk != "low" or not replay_gate_passed
        return ProposalRiskReport(
            risk_level=risk,
            reasons=list(dict.fromkeys(reasons)),
            auto_stage_allowed=auto_stage_allowed,
            apply_requires_approval=apply_requires_approval,
            replay_gate_passed=replay_gate_passed,
        )

    @staticmethod
    def _category_weight_reason(
        old_policy: dict[str, Any],
        new_policy: dict[str, Any],
    ) -> str:
        old_weights = old_policy.get("category_weights", {})
        new_weights = new_policy.get("category_weights", {})
        if not isinstance(old_weights, dict) or not isinstance(new_weights, dict):
            return ""
        for key in set(old_weights) | set(new_weights):
            old_value = float(old_weights.get(key, 0))
            new_value = float(new_weights.get(key, 0))
            if abs(new_value - old_value) >= 0.15:
                return "large category weight changes are high risk."
        return ""

    @staticmethod
    def _replay_gate(
        replay: ReplayEvaluation | dict[str, Any] | None,
    ) -> tuple[bool, str]:
        if replay is None:
            return False, "no replay evaluation; proposal can only be staged for review."
        evaluation = (
            replay
            if isinstance(replay, ReplayEvaluation)
            else ReplayEvaluation.model_validate(replay)
        )
        if evaluation.recommendation in {"reject-or-revise", "do-not-apply"}:
            return False, f"replay recommendation is {evaluation.recommendation}."
        if evaluation.false_positive_reduction < 0:
            return False, "replay would worsen false-positive reduction."
        if evaluation.missed_signal_reduction < 0:
            return False, "replay would worsen missed-signal reduction."
        return True, "replay gate passed without worsening tracked reductions."

    @staticmethod
    def _raise_if(
        current: ProposalRiskLevel,
        candidate: ProposalRiskLevel,
        condition: bool,
    ) -> ProposalRiskLevel:
        if not condition:
            return current
        order = {"low": 0, "medium": 1, "high": 2, "critical": 3}
        return candidate if order[candidate] > order[current] else current
