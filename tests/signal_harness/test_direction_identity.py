from __future__ import annotations

from signal_harness.intelligence.contracts import (
    DirectionCandidate,
    Evidence,
    ProductChange,
    SynthesisOutput,
)
from signal_harness.intelligence.direction_identity import (
    canonical_entity_family,
    independent_source_channel,
)
from signal_harness.intelligence.quality import prune_source_diversity_candidates


def _change(
    change_id: str,
    *,
    entity: str,
    source_type: str,
    source_name: str,
) -> ProductChange:
    return ProductChange(
        change_id=change_id,
        revision_id=f"rev-{change_id}",
        title=f"Change {change_id}",
        entity=entity,
        kind=source_type,
        published_at="2026-09-10T00:00:00+00:00",
        summary=f"变化 {change_id}",
        what_changed="外部来源记录了一项变化。",
        project_relation="context",
        relation_reason="与项目环境存在上下文关系。",
        attention="normal",
        topics=["test"],
        uncertainty="",
        interpretation_status="ready",
        relevant=True,
        evidence=[
            Evidence(
                evidence_id=f"ev-{change_id}",
                event_revision_id=1,
                source_name=source_name,
                source_type=source_type,
                url=f"https://example.test/{change_id}",
                authority="official",
                excerpt="evidence",
            )
        ],
    )


def _candidate(topic: str, ids: list[str]) -> DirectionCandidate:
    return DirectionCandidate(
        topic_key=topic,
        title=f"方向 {topic}",
        explanation="多条变化共同构成一个可观察方向。",
        supporting_change_ids=ids,
    )


def test_canonical_entity_family_collapses_publisher_and_repository_aliases() -> None:
    assert canonical_entity_family("OpenAI News") == "openai"
    assert canonical_entity_family("openai") == "openai"
    assert canonical_entity_family("openai/openai-python") == "openai"
    assert canonical_entity_family("Pydantic/pydantic") == "pydantic"
    assert canonical_entity_family("GitHub Changelog") == "github"


def test_independent_source_channel_collapses_same_github_repository_surfaces() -> None:
    issue = _change(
        "issue",
        entity="openai/openai-python",
        source_type="github_issue",
        source_name="openai/openai-python",
    ).evidence[0]
    release = _change(
        "release",
        entity="openai",
        source_type="github_release",
        source_name="openai/openai-python",
    ).evidence[0]
    registry = _change(
        "registry",
        entity="pydantic",
        source_type="package_registry",
        source_name="pydantic",
    ).evidence[0]

    assert independent_source_channel(issue) == "github:openai/openai-python"
    assert independent_source_channel(release) == "github:openai/openai-python"
    assert independent_source_channel(registry) == "registry:pydantic"


def test_direction_pruning_requires_real_entity_or_channel_diversity() -> None:
    changes = {
        "agents-news": _change(
            "agents-news",
            entity="OpenAI News",
            source_type="rss",
            source_name="OpenAI News",
        ),
        "agents-sdk": _change(
            "agents-sdk",
            entity="openai",
            source_type="github_release",
            source_name="openai/openai-python",
        ),
        "stream-issue-a": _change(
            "stream-issue-a",
            entity="openai/openai-python",
            source_type="github_issue",
            source_name="openai/openai-python",
        ),
        "stream-issue-b": _change(
            "stream-issue-b",
            entity="openai/openai-python",
            source_type="github_issue",
            source_name="openai/openai-python",
        ),
        "stream-release": _change(
            "stream-release",
            entity="openai",
            source_type="github_release",
            source_name="openai/openai-python",
        ),
        "pydantic-registry": _change(
            "pydantic-registry",
            entity="pydantic",
            source_type="package_registry",
            source_name="pydantic",
        ),
        "pydantic-issue-a": _change(
            "pydantic-issue-a",
            entity="pydantic/pydantic",
            source_type="github_issue",
            source_name="pydantic/pydantic",
        ),
        "pydantic-issue-b": _change(
            "pydantic-issue-b",
            entity="pydantic/pydantic",
            source_type="github_issue",
            source_name="pydantic/pydantic",
        ),
        "github-policy": _change(
            "github-policy",
            entity="GitHub Changelog",
            source_type="rss",
            source_name="GitHub Changelog",
        ),
        "langgraph-policy": _change(
            "langgraph-policy",
            entity="langchain-ai/langgraph",
            source_type="github_issue",
            source_name="langchain-ai/langgraph",
        ),
    }
    output = SynthesisOutput(
        brief=[
            {
                "text": "本期存在多个外部变化。",
                "supporting_change_ids": ["github-policy", "langgraph-policy"],
            }
        ],
        directions=[
            _candidate("single-vendor-release", ["agents-news", "agents-sdk"]),
            _candidate(
                "single-repo-streaming",
                ["stream-issue-a", "stream-issue-b", "stream-release"],
            ),
            _candidate(
                "pydantic-multi-channel",
                ["pydantic-registry", "pydantic-issue-a", "pydantic-issue-b"],
            ),
            _candidate("cross-actor-governance", ["github-policy", "langgraph-policy"]),
        ],
        featured_change_ids=["agents-sdk"],
    )

    filtered, dropped = prune_source_diversity_candidates(output, changes)

    assert dropped == 2
    assert [item.topic_key for item in filtered.directions] == [
        "pydantic-multi-channel",
        "cross-actor-governance",
    ]
