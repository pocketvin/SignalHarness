"""Supervisor orchestration across structured SignalHarness agents."""

from __future__ import annotations

import json
from typing import Any, Iterable

from signal_harness.agents.action import ActionAgent
from signal_harness.agents.classifier import ClassifierAgent
from signal_harness.agents.evidence import EvidenceAgent
from signal_harness.agents.impact import ImpactAgent
from signal_harness.runtime.tool_executor import SignalToolExecutor
from signal_harness.runtime.tracing import TraceRecorder
from signal_harness.signal.policy import decision_for_score
from signal_harness.signal.schemas import (
    FeedbackRecord,
    ScoreBreakdown,
    SignalAssessment,
    SignalCategory,
    SignalDecision,
    SignalEvent,
)


class SupervisorAgent:
    """Coordinate the business-agent chain and merge one assessment per event."""

    name = "SupervisorAgent"

    def __init__(
        self,
        tool_executor: SignalToolExecutor,
        *,
        trace: TraceRecorder | None = None,
    ) -> None:
        self.tool_executor = tool_executor
        self.trace = trace or TraceRecorder()
        self.classifier = ClassifierAgent()
        self.evidence = EvidenceAgent()
        self.impact = ImpactAgent()
        self.action = ActionAgent()

    async def assess_batch(
        self,
        events: Iterable[SignalEvent],
        *,
        project_profile: dict[str, Any],
        policy: dict[str, Any],
        seen_hashes: set[str] | None = None,
        feedback_history: Iterable[FeedbackRecord] = (),
    ) -> list[SignalAssessment]:
        assessments: list[SignalAssessment] = []
        feedback_payload = [item.model_dump(mode="json") for item in feedback_history]
        for event in events:
            with self.trace.step("classify", agent=self.classifier.name, input_count=1):
                classification = self.classifier.run(event, project_profile)
            with self.trace.step(
                "verify_evidence", agent=self.evidence.name, input_count=1
            ):
                evidence = self.evidence.run(event)
            with self.trace.step("score", agent="SignalScoreTool", input_count=1):
                score_result = await self.tool_executor.call(
                    "signal_score",
                    {
                        "event": event.model_dump(mode="json"),
                        "project_profile": project_profile,
                        "policy": policy,
                        "seen_hashes": sorted(seen_hashes or set()),
                        "feedback_history": feedback_payload,
                        "category": classification.category.value,
                    },
                )
                if score_result.is_error:
                    raise RuntimeError(score_result.output)
                breakdown = ScoreBreakdown.model_validate(json.loads(score_result.output))
            with self.trace.step("analyze_impact", agent=self.impact.name, input_count=1):
                impact = self.impact.run(event, project_profile, breakdown)
            decision = decision_for_score(impact.impact_score, policy)
            if classification.category is SignalCategory.NOISE:
                decision = SignalDecision.IGNORE
            with self.trace.step("plan_action", agent=self.action.name, input_count=1):
                action = self.action.run(event, classification.category, impact, decision)
            assessments.append(
                SignalAssessment(
                    event_id=event.event_id,
                    category=classification.category,
                    relevance_score=impact.relevance_score,
                    impact_score=impact.impact_score,
                    confidence=evidence.confidence,
                    affected_modules=impact.affected_modules,
                    evidence_urls=evidence.evidence_urls,
                    source_quality=evidence.source_quality,
                    reason=(
                        f"{classification.reason} {evidence.reason} {impact.reason}"
                    ),
                    action_items=action.action_items,
                    decision=decision,
                    score_breakdown=breakdown,
                )
            )
        return assessments
