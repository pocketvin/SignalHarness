from __future__ import annotations

from datetime import datetime, timezone

from signal_harness.signal.normalizer import normalize_github_event, normalize_rss_item


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
