"""Deterministic project-state facts for one observed external change."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Literal

from signal_harness.signal.schemas import SignalEvent
from signal_harness.signal.text_semantics import strip_untrusted_directives

VersionRelation = Literal["newer", "installed", "superseded", "unknown", "not_applicable"]


@dataclass(frozen=True)
class ProjectChangeState:
    """Facts derived from source identity plus the effective Project Profile."""

    dependency_name: str | None = None
    resolved_version: str | None = None
    event_version: str | None = None
    version_relation: VersionRelation = "not_applicable"
    protocol_name: str | None = None
    issue_fixed_for_installed_major: bool = False

    @property
    def direct_dependency(self) -> bool:
        return self.dependency_name is not None

    @property
    def already_satisfied(self) -> bool:
        return self.version_relation in {"installed", "superseded"} or self.issue_fixed_for_installed_major

    @property
    def newer_direct_release(self) -> bool:
        return self.direct_dependency and self.version_relation == "newer"


def resolve_project_change_state(
    event: SignalEvent,
    project_profile: dict[str, Any],
) -> ProjectChangeState:
    """Resolve direct-entity identity and conservative installed-version applicability."""

    protocol = _matched_protocol(event, project_profile)
    dependency = _matched_dependency(event, project_profile, protocol_name=protocol)
    if dependency is None:
        return ProjectChangeState(protocol_name=protocol)

    resolved = _resolved_dependency_version(dependency, project_profile)
    relation: VersionRelation = "not_applicable"
    if event.source_type == "github_release" and event.current_version and resolved:
        relation = _compare_stable_versions(event.current_version, resolved)
    elif event.source_type == "github_release":
        relation = "unknown"

    fixed = False
    if event.source_type == "github_issue" and resolved:
        fixed = _issue_says_fixed_for_major(event, resolved)

    return ProjectChangeState(
        dependency_name=dependency,
        resolved_version=resolved,
        event_version=event.current_version,
        version_relation=relation,
        protocol_name=protocol,
        issue_fixed_for_installed_major=fixed,
    )


def project_preference_importance(
    state: ProjectChangeState,
    project_profile: dict[str, Any],
) -> str | None:
    """Return the most specific active explicit importance for the matched entity."""

    candidates = {
        _normalize_identifier(value)
        for value in (state.dependency_name, state.protocol_name)
        if value
    }
    if not candidates:
        return None
    matched: str | None = None
    preferences = project_profile.get("importance_preferences", [])
    if not isinstance(preferences, list):
        return None
    for item in preferences:
        if not isinstance(item, dict):
            continue
        key = _normalize_identifier(str(item.get("scope_key") or ""))
        if key in candidates:
            matched = str(item.get("importance") or "normal").lower()
    return matched


def _matched_dependency(
    event: SignalEvent,
    project_profile: dict[str, Any],
    *,
    protocol_name: str | None,
) -> str | None:
    if event.source_type not in {"github_release", "github_issue", "package_registry"}:
        return None
    source_aliases = _source_aliases(event)
    dependencies = project_profile.get("dependencies", [])
    if not isinstance(dependencies, list):
        return None
    for value in dependencies:
        name = _dependency_name(value)
        if not name:
            continue
        normalized = _normalize_identifier(name)
        if normalized in source_aliases:
            return name
        if protocol_name and normalized == _acronym(protocol_name):
            return name
    return None


def _matched_protocol(event: SignalEvent, project_profile: dict[str, Any]) -> str | None:
    source_aliases = _source_aliases(event)
    protocols = project_profile.get("protocols", [])
    if not isinstance(protocols, list):
        return None
    for value in protocols:
        name = str(value).strip()
        if not name:
            continue
        aliases = _entity_aliases(name)
        if aliases & source_aliases:
            return name
    return None


def _source_aliases(event: SignalEvent) -> set[str]:
    values = [
        event.source_name,
        str(event.raw_payload.get("repository") or ""),
        str(event.raw_payload.get("repo") or ""),
        str(event.raw_payload.get("package_name") or ""),
    ]
    aliases: set[str] = set()
    for value in values:
        aliases.update(_entity_aliases(value))
        if "/" in value:
            aliases.update(_entity_aliases(value.rsplit("/", 1)[-1]))
    return {item for item in aliases if item}


def _dependency_name(value: object) -> str:
    if isinstance(value, dict):
        return str(value.get("name") or "").strip()
    return str(value).strip()


def _resolved_dependency_version(name: str, project_profile: dict[str, Any]) -> str | None:
    evidence = project_profile.get("dependency_evidence", [])
    if not isinstance(evidence, list):
        return None
    target = _normalize_identifier(name)
    for item in evidence:
        if not isinstance(item, dict):
            continue
        if _normalize_identifier(str(item.get("name") or "")) != target:
            continue
        resolved = str(item.get("resolved_version") or "").strip()
        return resolved or None
    return None


def _compare_stable_versions(event_version: str, resolved_version: str) -> VersionRelation:
    event_parts = _stable_version_parts(event_version)
    resolved_parts = _stable_version_parts(resolved_version)
    if event_parts is None or resolved_parts is None:
        return "unknown"
    width = max(len(event_parts), len(resolved_parts))
    event_padded = event_parts + (0,) * (width - len(event_parts))
    resolved_padded = resolved_parts + (0,) * (width - len(resolved_parts))
    if event_padded > resolved_padded:
        return "newer"
    if event_padded == resolved_padded:
        return "installed"
    return "superseded"


def _stable_version_parts(value: str) -> tuple[int, ...] | None:
    cleaned = value.strip().lower().removeprefix("v")
    match = re.fullmatch(r"(\d+(?:\.\d+)*)(?:[+-].*)?", cleaned)
    if not match:
        return None
    return tuple(int(part) for part in match.group(1).split("."))


def _issue_says_fixed_for_major(event: SignalEvent, resolved_version: str) -> bool:
    parts = _stable_version_parts(resolved_version)
    if not parts:
        return False
    major = parts[0]
    text = strip_untrusted_directives(f"{event.title} {event.content}").lower()
    patterns = (
        rf"\bfixed\s+(?:on|in)\s+v?{major}(?:\.x|\.\d+)?\b",
        rf"\balready\s+fixed\s+(?:on|in)\s+v?{major}(?:\.x|\.\d+)?\b",
    )
    return any(re.search(pattern, text) is not None for pattern in patterns)


def _entity_aliases(value: str) -> set[str]:
    normalized = _normalize_identifier(value)
    if not normalized:
        return set()
    aliases = {normalized}
    acronym = _acronym(value)
    if acronym:
        aliases.add(acronym)
    return aliases


def _normalize_identifier(value: str) -> str:
    lowered = value.strip().lower()
    lowered = re.sub(r"[^a-z0-9/._+-]+", "-", lowered)
    return lowered.strip("-._/")


def _acronym(value: str) -> str:
    normalized = _normalize_identifier(value)
    if not normalized:
        return ""
    if "/" in normalized:
        normalized = normalized.rsplit("/", 1)[-1]
    compact = re.sub(r"[^a-z0-9]+", "", normalized)
    if len(compact) <= 5:
        return compact
    words = [part for part in re.split(r"[^a-z0-9]+", normalized) if part]
    return "".join(part[0] for part in words) if len(words) >= 2 else ""
