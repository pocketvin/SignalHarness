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
    """Normalize GitHub release, issue, commit, or merged-PR payloads."""

    kind = event_kind or ("github_release" if "tag_name" in raw else "github_issue")
    source_name = repo or _text(raw.get("repository") or raw.get("repo"), "unknown-repo")
    association = str(raw.get("author_association") or "").strip().upper()
    if kind == "github_release":
        authority = "official" if raw.get("official", True) else "community"
        official = authority == "official"
    elif kind in {"github_commit", "github_pull_request"} or raw.get("official") is True:
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
    if kind == "github_commit":
        commit_value = raw.get("commit")
        commit: dict[str, Any] = commit_value if isinstance(commit_value, dict) else {}
        author_value = commit.get("author")
        author: dict[str, Any] = author_value if isinstance(author_value, dict) else {}
        committer_value = commit.get("committer")
        committer: dict[str, Any] = committer_value if isinstance(committer_value, dict) else {}
        message = _text(commit.get("message") or raw.get("message"))
        observed_at = committer.get("date") or author.get("date") or raw.get("updated_at")
        created_at = _datetime_value(author.get("date") or observed_at)
        updated_at = _datetime_value(committer.get("date") or observed_at)
        title = message.splitlines()[0] if message else f"Commit {_text(raw.get('sha'))[:12]}"
        change_kind = "new"
        current_version = None
    elif kind == "github_pull_request":
        observed_at = raw.get("merged_at") or raw.get("updated_at") or raw.get("created_at")
        title = _text(raw.get("title"), f"Pull request #{raw.get('number', '')}".strip())
        message = _text(raw.get("body"))
        change_kind = "new"
        current_version = None
    elif kind == "github_release":
        change_kind = "released"
        current_version = _text(raw.get("tag_name") or raw.get("name") or raw.get("title")) or None
        observed_at = raw.get("published_at") or raw.get("created_at")
        title = _text(raw.get("name") or raw.get("title") or raw.get("tag_name"))
        message = _text(raw.get("body") or raw.get("content"))
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
        title = _text(raw.get("name") or raw.get("title") or raw.get("tag_name"))
        message = _text(raw.get("body") or raw.get("content"))
    mapped = {
        **raw,
        "source_type": kind,
        "source_name": source_name,
        "title": title,
        "content": message,
        "url": raw.get("html_url") or raw.get("url") or "",
        "published_at": observed_at,
        "source_created_at": created_at or raw.get("created_at"),
        "source_updated_at": updated_at or raw.get("updated_at") or observed_at,
        "change_kind": change_kind,
        "current_version": current_version,
        "previous_version": raw.get("_previous_tag_name"),
        "repository_official": True,
        "source_authority": authority,
        "official": official,
    }
    return normalize_event(mapped, collected_at=collected_at)


def normalize_local_git_event(
    raw: dict[str, Any],
    *,
    repository: str | None = None,
    collected_at: datetime | None = None,
) -> SignalEvent:
    """Normalize one read-only local Git commit observation."""

    source_name = repository or _text(
        raw.get("repository_identity") or raw.get("repository"), "local-repository"
    )
    message = _text(raw.get("message"))
    sha = _text(raw.get("sha"))
    mapped = {
        **raw,
        "id": sha,
        "source_type": "local_git_commit",
        "source_name": source_name,
        "title": raw.get("title") or (message.splitlines()[0] if message else f"Commit {sha[:12]}"),
        "content": message,
        "url": raw.get("html_url") or "",
        "published_at": raw.get("committed_at") or raw.get("authored_at"),
        "source_created_at": raw.get("authored_at") or raw.get("committed_at"),
        "source_updated_at": raw.get("committed_at") or raw.get("authored_at"),
        "change_kind": "new",
        "official": True,
        "source_authority": "official",
        "project_owned": bool(raw.get("project_owned", True)),
    }
    return normalize_event(mapped, collected_at=collected_at)


def normalize_security_advisory_event(
    raw: dict[str, Any],
    *,
    collected_at: datetime | None = None,
) -> SignalEvent:
    """Normalize one OSV advisory matched to a concrete project dependency version."""

    advisory_id = _text(raw.get("id"), "unknown-advisory")
    package_name = _text(raw.get("matched_package"), "unknown-package")
    matched_version = _text(raw.get("matched_version")) or None
    published = _datetime_value(raw.get("published"))
    modified = _datetime_value(raw.get("modified"))
    change_kind: ChangeKind = (
        "updated"
        if isinstance(published, datetime)
        and isinstance(modified, datetime)
        and modified > published
        else "new"
    )
    mapped = {
        **raw,
        "source_type": "security_advisory",
        "source_name": "OSV",
        "title": raw.get("summary") or f"{advisory_id} affects {package_name}",
        "content": raw.get("details") or raw.get("summary") or "",
        "url": raw.get("osv_url") or f"https://osv.dev/vulnerability/{advisory_id}",
        "published_at": raw.get("modified") or raw.get("published"),
        "source_created_at": raw.get("published"),
        "source_updated_at": raw.get("modified"),
        "change_kind": change_kind,
        "current_version": matched_version,
        "official": True,
        "source_authority": "official",
        "package_name": package_name,
    }
    return normalize_event(mapped, collected_at=collected_at)


def normalize_package_registry_event(
    raw: dict[str, Any],
    *,
    package_name: str | None = None,
    collected_at: datetime | None = None,
) -> SignalEvent:
    """Normalize one package-registry release record."""

    package = package_name or _text(raw.get("package_name") or raw.get("source_name"))
    mapped = {
        **raw,
        "source_type": "package_registry",
        "source_name": package or "unknown-package",
        "title": raw.get("title") or f"{package} {raw.get('current_version') or ''}".strip(),
        "content": raw.get("content") or "",
        "url": raw.get("url") or "",
        "published_at": raw.get("published_at") or raw.get("upload_time"),
        "source_created_at": raw.get("published_at") or raw.get("upload_time"),
        "source_updated_at": raw.get("published_at") or raw.get("upload_time"),
        "change_kind": "released",
        "current_version": raw.get("current_version") or raw.get("version"),
        "previous_version": raw.get("previous_version"),
        "official": True,
        "source_authority": "official",
        "package_name": package,
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
