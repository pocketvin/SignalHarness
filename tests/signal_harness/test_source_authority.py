from __future__ import annotations

from pathlib import Path

from signal_harness.agent_integration.runner import LLMAgentTeamRunner
from signal_harness.agent_integration.schemas import ContextEvidenceItem, ContextEvidenceOutput
from signal_harness.agents.evidence import EvidenceAgent
from signal_harness.signal.normalizer import normalize_github_event
from signal_harness.signal.policy import load_signal_policy
from signal_harness.signal.schemas import SignalCategory, SourceQuality
from signal_harness.signal.scorer import source_score
from signal_harness.agent_integration.scoring_bridge import _priority_floor_score


def test_github_issue_authority_distinguishes_community_maintainer_and_release(
    project_root: Path,
) -> None:
    policy = load_signal_policy(project_root / "configs/signal_policy.yaml")
    community = normalize_github_event(
        {
            "number": 1,
            "title": "Possible vulnerability in optional example",
            "body": "Community report",
            "html_url": "https://github.com/org/repo/issues/1",
            "author_association": "NONE",
        },
        repo="org/repo",
        event_kind="github_issue",
    )
    maintainer = normalize_github_event(
        {
            "number": 2,
            "title": "Maintainer reports runtime regression",
            "body": "Maintainer report",
            "html_url": "https://github.com/org/repo/issues/2",
            "author_association": "MEMBER",
        },
        repo="org/repo",
        event_kind="github_issue",
    )
    release = normalize_github_event(
        {
            "id": 3,
            "name": "v1.2.3",
            "html_url": "https://github.com/org/repo/releases/tag/v1.2.3",
        },
        repo="org/repo",
        event_kind="github_release",
    )

    assert community.raw_payload["repository_official"] is True
    assert community.raw_payload["source_authority"] == "community"
    assert community.raw_payload["official"] is False
    assert EvidenceAgent().run(community).source_quality is SourceQuality.COMMUNITY
    assert source_score(community, policy) == 50

    assert maintainer.raw_payload["source_authority"] == "maintainer"
    assert EvidenceAgent().run(maintainer).source_quality is SourceQuality.MAINTAINER
    assert source_score(maintainer, policy) == 62

    assert release.raw_payload["source_authority"] == "official"
    assert EvidenceAgent().run(release).source_quality is SourceQuality.OFFICIAL


def test_python_clamps_llm_provenance_and_priority_floor_for_community_issue(
    project_root: Path,
) -> None:
    event = normalize_github_event(
        {
            "number": 4,
            "title": "CVE vulnerability supply chain claim from a user",
            "body": "Unverified community claim",
            "html_url": "https://github.com/org/repo/issues/4",
            "author_association": "NONE",
        },
        repo="org/repo",
        event_kind="github_issue",
    )
    evidence = ContextEvidenceOutput(
        results=[
            ContextEvidenceItem(
                event_id=event.event_id,
                evidence_urls=[event.url],
                context_summary="claim",
                confidence=0.99,
                source_quality=SourceQuality.OFFICIAL,
            )
        ]
    )

    clamped = LLMAgentTeamRunner._clamp_evidence_source_authority(evidence, [event])
    item = clamped.results[0]
    assert item.source_quality is SourceQuality.COMMUNITY
    assert item.confidence <= 0.65

    policy = load_signal_policy(project_root / "configs/signal_policy.yaml")
    assert (
        _priority_floor_score(
            event=event,
            category=SignalCategory.SECURITY_SUPPLY_CHAIN,
            source_quality=item.source_quality.value,
            policy=policy,
        )
        is None
    )
