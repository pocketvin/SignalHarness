"""Stable source-owned identities for releases, Git facts, and advisories."""

from __future__ import annotations

from dataclasses import dataclass
import re

from packaging.utils import canonicalize_name
from packaging.version import InvalidVersion, Version

from signal_harness.signal.schemas import SignalEvent


@dataclass(frozen=True)
class PackageIdentity:
    registry: str
    name: str


@dataclass(frozen=True)
class GitChangeIdentity:
    repository: str
    commit_sha: str


@dataclass(frozen=True)
class SecurityAdvisoryIdentity:
    advisory_id: str


def release_package_identity(event: SignalEvent) -> PackageIdentity | None:
    """Return package identity only when the source observation owns that fact."""

    raw = event.raw_payload
    if event.source_type == "package_registry":
        registry = str(raw.get("registry") or "").strip().lower()
        name = str(raw.get("package_name") or event.source_name).strip()
    elif event.source_type == "github_release":
        registry = str(raw.get("package_registry") or "").strip().lower()
        name = str(raw.get("package_name") or "").strip()
    else:
        return None
    if not registry or not name:
        return None
    normalized = canonicalize_name(name)
    return PackageIdentity(registry=registry, name=normalized) if normalized else None


def release_version_identity(value: str) -> str:
    """Normalize release versions without making invalid versions unusable."""

    raw = value.strip()
    try:
        return str(Version(raw))
    except InvalidVersion:
        return raw.lower().removeprefix("v")


def git_change_identity(event: SignalEvent) -> GitChangeIdentity | None:
    """Return a commit identity shared by local Git, GitHub commits, and merged PRs."""

    if event.source_type not in {"local_git_commit", "github_commit", "github_pull_request"}:
        return None
    raw = event.raw_payload
    if event.source_type == "github_pull_request":
        if not raw.get("merged_at"):
            return None
        commit_sha = str(raw.get("merge_commit_sha") or "").strip().lower()
    else:
        commit_sha = str(raw.get("sha") or raw.get("commit_sha") or "").strip().lower()
    repository = normalize_repository_identity(
        str(
            raw.get("repository_identity")
            or raw.get("repository")
            or raw.get("repo")
            or event.source_name
        )
    )
    if not repository or not re.fullmatch(r"[0-9a-f]{7,64}", commit_sha):
        return None
    return GitChangeIdentity(repository=repository, commit_sha=commit_sha)


def security_advisory_identity(event: SignalEvent) -> SecurityAdvisoryIdentity | None:
    """Prefer globally recognizable advisory aliases for deterministic aggregation."""

    if event.source_type != "security_advisory":
        return None
    raw = event.raw_payload
    raw_aliases = raw.get("aliases", [])
    aliases = (
        [str(value).strip().upper() for value in raw_aliases if str(value).strip()]
        if isinstance(raw_aliases, list)
        else []
    )
    advisory_id = str(raw.get("id") or "").strip().upper()
    for prefix in ("CVE-", "GHSA-"):
        match = next((value for value in aliases if value.startswith(prefix)), None)
        if match:
            return SecurityAdvisoryIdentity(advisory_id=match)
    if advisory_id:
        return SecurityAdvisoryIdentity(advisory_id=advisory_id)
    return None


def github_repository_from_remote(value: str) -> str | None:
    """Extract ``owner/repo`` from common GitHub remote URL forms."""

    raw = value.strip()
    patterns = (
        r"^https?://github\.com/([^/]+/[^/]+?)(?:\.git)?/?$",
        r"^git@github\.com:([^/]+/[^/]+?)(?:\.git)?$",
        r"^ssh://git@github\.com/([^/]+/[^/]+?)(?:\.git)?/?$",
    )
    for pattern in patterns:
        match = re.match(pattern, raw, flags=re.IGNORECASE)
        if match:
            return match.group(1).removesuffix(".git").strip("/").lower()
    return None


def normalize_repository_identity(value: str) -> str:
    """Normalize repository identity without treating arbitrary remotes as GitHub repos."""

    github = github_repository_from_remote(value)
    if github:
        return github
    normalized = value.strip().replace("\\", "/").removesuffix(".git").rstrip("/")
    return normalized.lower()
