from __future__ import annotations

from datetime import datetime, timezone

from signal_harness.signal.clustering import SignalClusterer
from signal_harness.signal.schemas import SignalEvent

NOW = datetime(2026, 6, 20, tzinfo=timezone.utc)


def _event(
    event_id: str,
    *,
    source_type: str,
    source_name: str,
    title: str,
    content: str,
    url: str,
    published_at: datetime | None = NOW,
) -> SignalEvent:
    return SignalEvent.model_validate(
        {
            "event_id": event_id,
            "source_type": source_type,
            "source_name": source_name,
            "title": title,
            "content": content,
            "url": url,
            "published_at": published_at,
            "raw_payload": {},
            "collected_at": NOW,
        }
    )


def _cluster_sizes(events: list[SignalEvent]) -> list[int]:
    return sorted(
        len(cluster.related_event_ids)
        for cluster in SignalClusterer().cluster(events)
    )


def test_same_github_repo_unrelated_issues_do_not_cluster() -> None:
    events = [
        _event(
            "issue-1",
            source_type="github_issue",
            source_name="ExampleOrg/SignalRuntime",
            title="Terminal theme preference bug",
            content="Dark mode color contrast in the terminal footer.",
            url="https://github.com/ExampleOrg/SignalRuntime/issues/1",
        ),
        _event(
            "issue-2",
            source_type="github_issue",
            source_name="ExampleOrg/SignalRuntime",
            title="OAuth callback timeout",
            content="Login redirect occasionally stalls after browser authentication.",
            url="https://github.com/ExampleOrg/SignalRuntime/issues/2",
        ),
    ]

    assert _cluster_sizes(events) == [1, 1]


def test_same_domain_different_topics_do_not_cluster() -> None:
    events = [
        _event(
            "article-1",
            source_type="rss",
            source_name="Example Engineering",
            title="Checkpoint recovery patterns",
            content="Durable state recovery for agent workflows.",
            url="https://engineering.example.com/checkpoints",
        ),
        _event(
            "article-2",
            source_type="rss",
            source_name="Example Engineering",
            title="Design system typography refresh",
            content="Font weights and spacing tokens changed for docs pages.",
            url="https://engineering.example.com/typography",
        ),
    ]

    assert _cluster_sizes(events) == [1, 1]


def test_cross_source_same_topic_clusters_when_time_and_tokens_match() -> None:
    events = [
        _event(
            "release",
            source_type="github_release",
            source_name="ExampleOrg/SignalRuntime",
            title="Checkpoint persistence release",
            content="Durable checkpoint persistence and recovery landed.",
            url="https://github.com/ExampleOrg/SignalRuntime/releases/tag/v2",
        ),
        _event(
            "rss",
            source_type="rss",
            source_name="Independent Agent Engineering",
            title="Reviewing checkpoint persistence",
            content="Commentary on durable checkpoint recovery in SignalRuntime.",
            url="https://engineering.example.com/checkpoint-persistence",
        ),
        _event(
            "web",
            source_type="web_change",
            source_name="SignalRuntime product page",
            title="Checkpoint persistence docs update",
            content="Product page now describes durable checkpoint recovery.",
            url="https://signalruntime.example.com/checkpoint",
        ),
    ]

    assert _cluster_sizes(events) == [3]


def test_missing_published_at_does_not_relax_clustering() -> None:
    events = [
        _event(
            "undated-1",
            source_type="github_issue",
            source_name="ExampleOrg/SignalRuntime",
            title="Checkpoint persistence recovery",
            content="Durable checkpoint recovery discussion.",
            url="https://github.com/ExampleOrg/SignalRuntime/issues/10",
            published_at=None,
        ),
        _event(
            "undated-2",
            source_type="rss",
            source_name="Independent Agent Engineering",
            title="Checkpoint persistence recovery",
            content="Durable checkpoint recovery commentary.",
            url="https://engineering.example.com/checkpoint-recovery",
        ),
    ]

    assert _cluster_sizes(events) == [1, 1]


def test_source_name_alone_does_not_cluster_events() -> None:
    events = [
        _event(
            "release-note",
            source_type="github_release",
            source_name="ExampleOrg/SignalRuntime",
            title="Checkpoint persistence release",
            content="Durable checkpoint recovery helpers shipped.",
            url="https://github.com/ExampleOrg/SignalRuntime/releases/tag/v2",
        ),
        _event(
            "community-issue",
            source_type="github_issue",
            source_name="ExampleOrg/SignalRuntime",
            title="Terminal keyboard shortcut request",
            content="Users want a quicker shortcut for toggling the terminal panel.",
            url="https://github.com/ExampleOrg/SignalRuntime/issues/99",
        ),
    ]

    assert _cluster_sizes(events) == [1, 1]
