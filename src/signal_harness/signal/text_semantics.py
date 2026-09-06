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
