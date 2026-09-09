"""Stable source-owned identities for public release facts."""

from __future__ import annotations

from dataclasses import dataclass

from packaging.utils import canonicalize_name
from packaging.version import InvalidVersion, Version

from signal_harness.signal.schemas import SignalEvent


@dataclass(frozen=True)
class PackageIdentity:
    registry: str
    name: str


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
