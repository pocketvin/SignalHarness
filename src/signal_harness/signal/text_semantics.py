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


_PROMPT_INJECTION_PATTERNS = (
    re.compile(
        r"\b(?:ignore|disregard|override)\b.{0,48}\b(?:previous|system|developer|all)\b.{0,32}\binstructions?\b",
        re.I,
    ),
    re.compile(
        r"\b(?:reveal|print|return|replace|override)\b.{0,48}\b(?:system prompt|developer message)\b",
        re.I,
    ),
    re.compile(r"^\s*(?:please\s+)?(?:call|use|execute|request)\s+(?:all\s+)?tools?\b", re.I),
    re.compile(
        r"^\s*(?:please\s+)?(?:mark|classify|label|return)\b.{0,64}\b(?:critical|alert|security|cve)\b",
        re.I,
    ),
)


def untrusted_instruction_patterns(text: str) -> list[str]:
    """Return stable labels for instruction-like content embedded in external data."""

    labels: list[str] = []
    for index, pattern in enumerate(_PROMPT_INJECTION_PATTERNS, start=1):
        if pattern.search(text):
            labels.append(f"external_instruction_pattern_{index}")
    return labels


def strip_untrusted_directives(text: str) -> str:
    """Exclude instruction-like sentences from deterministic semantic matching only."""

    parts = re.split(r"(?<=[.!?])\s+|[\r\n]+", text)
    kept = [part for part in parts if part.strip() and not untrusted_instruction_patterns(part)]
    return " ".join(kept)


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
        semantic = title
    elif source_type == "github_release":
        semantic = f"{title} {_runtime_release_content(content)}"
    else:
        semantic = f"{title} {content}"
    return f"{prefix}{strip_untrusted_directives(semantic)}".lower()


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
