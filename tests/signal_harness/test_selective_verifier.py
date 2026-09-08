from __future__ import annotations

from datetime import datetime, timezone

from signal_harness.agent_integration.runner import LLMAgentTeamRunner
from signal_harness.agent_integration.schemas import (
    ActionItem,
    ActionOutput,
    ContextEvidenceItem,
    ContextEvidenceOutput,
    ImpactItem,
    ImpactOutput,
    VerificationItem,
    VerificationOutput,
)
from signal_harness.signal.schemas import SignalEvent, SourceQuality


def _event() -> SignalEvent:
    now = datetime.now(timezone.utc)
    return SignalEvent(
        event_id="verify-1",
        source_type="github_issue",
        source_name="mcp",
        title="Possible protocol regression",
        content="Community report with uncertain impact.",
        url="https://example.com/issue",
        raw_payload={"source_authority": "community"},
        collected_at=now,
        published_at=now,
    )


def _outputs() -> tuple[ContextEvidenceOutput, ImpactOutput, ActionOutput]:
    evidence = ContextEvidenceOutput(
        results=[
            ContextEvidenceItem(
                event_id="verify-1",
                evidence_urls=["https://example.com/issue"],
                context_summary="community report",
                confidence=0.8,
                source_quality=SourceQuality.COMMUNITY,
            )
        ]
    )
    impact = ImpactOutput(
        results=[
            ImpactItem(
                event_id="verify-1",
                affected_modules=["mcp"],
                semantic_relevance=90,
                risk_level="high",
                impact_reason="possible impact",
            )
        ]
    )
    action = ActionOutput(
        results=[
            ActionItem(
                event_id="verify-1",
                action_items=["Escalate immediately"],
                critic_notes="initial",
                approval_required=True,
                requested_actions=["send_team_notification"],
            )
        ]
    )
    return evidence, impact, action


def test_verifier_can_only_apply_conservative_caps() -> None:
    evidence, impact, action = _outputs()
    verification = VerificationOutput(
        results=[
            VerificationItem(
                event_id="verify-1",
                supported=False,
                confidence_cap=0.4,
                impact_overstated=True,
                action_overstated=True,
            )
        ]
    )

    checked_evidence, checked_impact, checked_action = LLMAgentTeamRunner._apply_verification(
        evidence, impact, action, verification
    )

    assert checked_evidence.results[0].confidence == 0.4
    assert checked_impact.results[0].semantic_relevance == 35
    assert checked_impact.results[0].risk_level == "low"
    assert checked_impact.results[0].affected_modules == []
    assert checked_action.results[0].requested_actions == []
    assert checked_action.results[0].approval_required is False


def test_selective_verifier_gate_targets_uncertain_semantic_results() -> None:
    event = _event()
    evidence, impact, _ = _outputs()
    evidence = evidence.model_copy(
        update={
            "results": [
                evidence.results[0].model_copy(update={"confidence": 0.55})
            ]
        }
    )

    selected = LLMAgentTeamRunner._selective_verifier_event_ids(
        [event], evidence, impact
    )

    assert selected == {"verify-1"}
