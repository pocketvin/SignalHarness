"""Normalize heterogeneous source records into SignalEvent objects."""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any, cast

from signal_harness.signal.schemas import ChangeKind, SignalEvent


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _text(value: Any, fallback: str = "") -> str:
    if value is None:
        return fallback
    rendered = str(value).strip()
    return rendered or fallback


def _event_id(source_type: str, source_name: str, title: str, url: str) -> str:
    payload = "|".join((source_type, source_name, title, url)).lower()
    return f"{source_type}-{hashlib.sha256(payload.encode('utf-8')).hexdigest()[:16]}"


def _datetime_value(value: Any) -> Any:
    if not isinstance(value, str) or not value.strip():
        return value
    raw = value.strip()
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        try:
            return parsedate_to_datetime(raw)
        except (TypeError, ValueError):
            return value


def normalize_event(raw: dict[str, Any], *, collected_at: datetime | None = None) -> SignalEvent:
    """Normalize a known raw source shape, with safe fallbacks for missing fields."""

    if not isinstance(raw, dict):
        raise TypeError("raw event must be a mapping")
    timestamp = collected_at or _now()

    required = {"event_id", "source_type", "source_name", "title", "collected_at"}
    if required.issubset(raw):
        return SignalEvent.model_validate(raw)

    source_type = _text(raw.get("source_type") or raw.get("type"), "web_change")
    source_name = _text(
        raw.get("source_name")
        or raw.get("repository")
        or raw.get("repo")
        or raw.get("feed_title")
        or raw.get("source"),
        "unknown-source",
    )
    title = _text(raw.get("title") or raw.get("name"), "Untitled signal")
    content = _text(
        raw.get("content") or raw.get("body") or raw.get("description") or raw.get("summary"),
    )
    url = _text(raw.get("url") or raw.get("html_url") or raw.get("link"))
    published_at = _datetime_value(
        raw.get("published_at")
        or raw.get("updated_at")
        or raw.get("published")
        or raw.get("created_at")
    )
    source_created_at = _datetime_value(
        raw.get("source_created_at") or raw.get("created_at") or raw.get("published")
    )
    source_updated_at = _datetime_value(raw.get("source_updated_at") or raw.get("updated_at"))
    event_id = _text(raw.get("event_id") or raw.get("id"))
    if not event_id:
        event_id = _event_id(source_type, source_name, title, url)

    return SignalEvent(
        event_id=event_id,
        source_type=source_type,
        source_name=source_name,
        title=title,
        content=content,
        url=url,
        published_at=published_at,
        change_kind=cast(ChangeKind, str(raw.get("change_kind") or "unknown")),
        source_created_at=source_created_at,
        source_updated_at=source_updated_at,
        current_version=_text(raw.get("current_version")) or None,
        previous_version=_text(raw.get("previous_version")) or None,
        raw_payload=dict(raw),
        collected_at=timestamp,
    )


def normalize_github_event(
    raw: dict[str, Any],
    *,
    repo: str | None = None,
    event_kind: str | None = None,
    collected_at: datetime | None = None,
) -> SignalEvent:
    """Normalize GitHub release or issue payloads."""

    kind = event_kind or ("github_release" if "tag_name" in raw else "github_issue")
    source_name = repo or _text(raw.get("repository") or raw.get("repo"), "unknown-repo")
    association = str(raw.get("author_association") or "").strip().upper()
    if kind == "github_release":
        authority = "official" if raw.get("official", True) else "community"
        official = authority == "official"
    elif raw.get("official") is True:
        authority = "official"
        official = True
    elif association in {"OWNER", "MEMBER", "COLLABORATOR"}:
        authority = "maintainer"
        official = False
    else:
        authority = "community"
        official = False
    created_at = _datetime_value(raw.get("created_at"))
    updated_at = _datetime_value(raw.get("updated_at"))
    if kind == "github_release":
        change_kind = "released"
        current_version = _text(raw.get("tag_name") or raw.get("name") or raw.get("title")) or None
        observed_at = raw.get("published_at") or raw.get("created_at")
    else:
        change_kind = (
            "updated"
            if isinstance(created_at, datetime)
            and isinstance(updated_at, datetime)
            and updated_at > created_at
            else "new"
        )
        current_version = None
        observed_at = raw.get("updated_at") or raw.get("created_at")
    mapped = {
        **raw,
        "source_type": kind,
        "source_name": source_name,
        "title": raw.get("name") or raw.get("title") or raw.get("tag_name"),
        "content": raw.get("body") or raw.get("content") or "",
        "url": raw.get("html_url") or raw.get("url") or "",
        "published_at": observed_at,
        "source_created_at": raw.get("created_at"),
        "source_updated_at": raw.get("updated_at"),
        "change_kind": change_kind,
        "current_version": current_version,
        "previous_version": raw.get("_previous_tag_name"),
        "repository_official": True,
        "source_authority": authority,
        "official": official,
    }
    return normalize_event(mapped, collected_at=collected_at)


def normalize_rss_item(
    raw: dict[str, Any],
    *,
    feed_name: str | None = None,
    collected_at: datetime | None = None,
) -> SignalEvent:
    """Normalize an RSS/Atom item mapping."""

    published = _datetime_value(raw.get("published"))
    updated = _datetime_value(raw.get("updated"))
    change_kind = (
        "updated"
        if isinstance(published, datetime) and isinstance(updated, datetime) and updated > published
        else "published"
    )
    mapped = {
        **raw,
        "source_type": "rss",
        "source_name": feed_name or raw.get("feed_title") or raw.get("source") or "unknown-feed",
        "title": raw.get("title"),
        "content": raw.get("summary") or raw.get("description") or raw.get("content") or "",
        "url": raw.get("link") or raw.get("url") or "",
        "published_at": raw.get("updated") or raw.get("published"),
        "source_created_at": raw.get("published"),
        "source_updated_at": raw.get("updated"),
        "change_kind": change_kind,
    }
    return normalize_event(mapped, collected_at=collected_at)
