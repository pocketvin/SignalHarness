"""Small text-semantic helpers shared by deterministic and scripted paths."""

from __future__ import annotations

import re
from collections.abc import Iterable

_NEGATION = re.compile(r"\b(?:no|not|without|never)\b[^.!?;]{0,48}$")


def contains_affirmed_term(text: str, term: str) -> bool:
    """Return true when a bounded term occurs outside a nearby negation phrase."""

    cleaned = term.strip().lower()
    if not cleaned:
        return False
    lowered = text.lower()
    pattern = rf"(?<![a-z0-9]){re.escape(cleaned)}(?![a-z0-9])"
    for match in re.finditer(pattern, lowered):
        prefix = lowered[max(0, match.start() - 48) : match.start()]
        if _NEGATION.search(prefix):
            continue
        if "unrelated to" in prefix:
            continue
        return True
    return False


def any_affirmed_term(text: str, terms: Iterable[str]) -> bool:
    """Return true when at least one candidate term is affirmed."""

    return any(contains_affirmed_term(text, term) for term in terms)


_RELEASE_EXCLUDED_HEADINGS = {"documentation", "docs", "chore", "chores"}


def source_semantic_text(
    *,
    source_type: str,
    title: str,
    content: str,
    source_name: str = "",
    include_source: bool = False,
) -> str:
    """Return deterministic semantic text while suppressing known source-noise regions."""

    prefix = f"{source_name} " if include_source and source_name else ""
    if source_type == "github_issue":
        return f"{prefix}{title}".lower()
    if source_type == "github_release":
        return f"{prefix}{title} {_runtime_release_content(content)}".lower()
    return f"{prefix}{title} {content}".lower()


def _runtime_release_content(content: str) -> str:
    """Drop docs/chore sections so release metadata does not masquerade as runtime risk."""

    kept: list[str] = []
    excluded = False
    for line in content.splitlines():
        match = re.match(r"^#{2,6}\s+(.+?)\s*$", line.strip())
        if match:
            heading = re.sub(r"[^a-z]+", " ", match.group(1).lower()).strip()
            excluded = any(
                heading == marker or heading.startswith(marker + " ")
                for marker in _RELEASE_EXCLUDED_HEADINGS
            )
            continue
        if not excluded:
            kept.append(line)
    return " ".join(kept)
