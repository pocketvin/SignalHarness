from __future__ import annotations

from datetime import datetime, timezone

from signal_harness.signal.deltas import annotate_release_lineage
from signal_harness.signal.normalizer import (
    normalize_github_event,
    normalize_package_registry_event,
    normalize_rss_item,
)


NOW = datetime(2026, 6, 25, tzinfo=timezone.utc)


def test_github_raw_event_becomes_signal_event() -> None:
    event = normalize_github_event(
        {
            "id": 42,
            "tag_name": "v1.2.0",
            "name": "Checkpoint migration",
            "body": "Adds durable checkpoint migrations.",
            "html_url": "https://github.com/example/repo/releases/tag/v1.2.0",
            "published_at": "2026-06-24T10:00:00Z",
        },
        repo="example/repo",
        event_kind="github_release",
        collected_at=NOW,
    )

    assert event.source_type == "github_release"
    assert event.source_name == "example/repo"
    assert event.title == "Checkpoint migration"
    assert event.raw_payload["tag_name"] == "v1.2.0"
    assert event.change_kind == "released"
    assert event.current_version == "v1.2.0"


def test_rss_raw_item_becomes_signal_event() -> None:
    event = normalize_rss_item(
        {
            "title": "Evidence-grounded agents",
            "summary": "Why traceable evidence matters.",
            "link": "https://example.com/evidence",
            "published": "2026-06-24T10:00:00Z",
        },
        feed_name="Expert Feed",
        collected_at=NOW,
    )

    assert event.source_type == "rss"
    assert event.source_name == "Expert Feed"
    assert event.url == "https://example.com/evidence"


def test_missing_fields_use_readable_fallbacks() -> None:
    event = normalize_rss_item({}, collected_at=NOW)

    assert event.title == "Untitled signal"
    assert event.source_name == "unknown-feed"
    assert event.event_id.startswith("rss-")


def test_github_issue_uses_updated_at_as_observed_change_time() -> None:
    event = normalize_github_event(
        {
            "id": 99,
            "title": "Event loop regression",
            "body": "Observed after upgrading the SDK.",
            "html_url": "https://github.com/example/repo/issues/99",
            "created_at": "2026-06-20T10:00:00Z",
            "updated_at": "2026-06-24T12:00:00Z",
            "author_association": "NONE",
        },
        repo="example/repo",
        event_kind="github_issue",
        collected_at=NOW,
    )

    assert event.change_kind == "updated"
    assert event.published_at == datetime(2026, 6, 24, 12, 0, tzinfo=timezone.utc)
    assert event.source_created_at == datetime(2026, 6, 20, 10, 0, tzinfo=timezone.utc)
    assert event.source_updated_at == datetime(2026, 6, 24, 12, 0, tzinfo=timezone.utc)


def test_release_lineage_infers_previous_version_from_same_batch() -> None:
    newest = normalize_github_event(
        {
            "id": 2,
            "tag_name": "v2.0.0",
            "name": "v2.0.0",
            "published_at": "2026-06-24T10:00:00Z",
        },
        repo="example/repo",
        event_kind="github_release",
        collected_at=NOW,
    )
    older = normalize_github_event(
        {
            "id": 1,
            "tag_name": "v1.9.0",
            "name": "v1.9.0",
            "published_at": "2026-06-20T10:00:00Z",
        },
        repo="example/repo",
        event_kind="github_release",
        collected_at=NOW,
    )

    annotated = annotate_release_lineage([older, newest])
    by_id = {event.event_id: event for event in annotated}
    assert by_id["2"].previous_version == "v1.9.0"
    assert by_id["2"].current_version == "v2.0.0"
    assert by_id["1"].previous_version is None


def test_rss_updated_item_preserves_publish_and_update_times() -> None:
    event = normalize_rss_item(
        {
            "title": "Updated guidance",
            "summary": "Revised provider guidance.",
            "link": "https://example.com/update",
            "published": "2026-06-20T10:00:00Z",
            "updated": "2026-06-24T10:00:00Z",
        },
        feed_name="Official Feed",
        collected_at=NOW,
    )

    assert event.change_kind == "updated"
    assert event.source_created_at == datetime(2026, 6, 20, 10, 0, tzinfo=timezone.utc)
    assert event.source_updated_at == datetime(2026, 6, 24, 10, 0, tzinfo=timezone.utc)


def test_package_registry_release_becomes_signal_event() -> None:
    event = normalize_package_registry_event(
        {
            "package_name": "mcp",
            "current_version": "2.2.0",
            "previous_version": "2.1.1",
            "published_at": "2026-09-08T10:00:00Z",
            "url": "https://pypi.org/project/mcp/2.2.0/",
            "content": "PyPI release 2.2.0 for mcp.",
        },
        collected_at=NOW,
    )
    assert event.source_type == "package_registry"
    assert event.source_name == "mcp"
    assert event.current_version == "2.2.0"
    assert event.previous_version == "2.1.1"
    assert event.raw_payload["source_authority"] == "official"
