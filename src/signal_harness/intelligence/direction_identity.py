"""Deterministic identity helpers for EnvironmentDirection support diversity."""

from __future__ import annotations

import re

from signal_harness.intelligence.contracts import Evidence

_GENERIC_ENTITY_SUFFIXES = {"api", "changelog", "news", "release", "releases", "sdk"}


def canonical_entity_family(entity: str) -> str:
    """Collapse obvious publisher/repository aliases without semantic guessing.

    Examples: ``OpenAI News`` and ``openai`` both become ``openai``;
    ``openai/openai-python`` becomes the repository owner ``openai``.
    """
    value = entity.casefold().strip()
    if "/" in value:
        value = value.split("/", 1)[0]
    tokens = [token for token in re.split(r"[^a-z0-9_.+-]+", value) if token]
    while len(tokens) > 1 and tokens[-1] in _GENERIC_ENTITY_SUFFIXES:
        tokens.pop()
    return " ".join(tokens) or value


def independent_source_channel(evidence: Evidence) -> str:
    """Count one GitHub repository as one source regardless of Issue/Release surface."""
    source_type = evidence.source_type.casefold().strip()
    source_name = evidence.source_name.casefold().strip()
    if source_type.startswith("github_"):
        return f"github:{source_name}"
    if source_type == "package_registry":
        return f"registry:{source_name}"
    if source_type in {"rss", "atom"}:
        return f"feed:{source_name}"
    if source_type.startswith("web"):
        return f"web:{source_name}"
    return f"{source_type}:{source_name}"
