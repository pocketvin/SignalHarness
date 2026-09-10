"""Deterministic semantic guards around one global environment synthesis call.

These guards do not decide whether a trend is true. They reject classes of claims that
can be proven structurally invalid from the frozen corpus (wrong IDs, internal-project
activity used as external evidence, contradictory dates, or nearly duplicate directions).
"""

from __future__ import annotations

import re
from datetime import datetime
from itertools import combinations
from typing import Iterable

from signal_harness.intelligence.contracts import (
    DirectionCandidate,
    GroundedClaim,
    ProductChange,
    SynthesisOutput,
)

_SAME_DAY = re.compile(r"同日|同一天|同一日|当天")
_SHORT_WINDOW = re.compile(r"短时间内|短期内")
# Direction velocity belongs to the guarded Direction.state computed from comparable scans.
_DIRECTION_VELOCITY = re.compile(
    r"加速|减速|增强|减弱|走强|走弱|升温|降温|激增|骤降|明显增长|显著增长|明显下降|显著下降|翻倍|同比|环比"
)
_LATIN = re.compile(r"[A-Za-z][A-Za-z0-9_.+/-]{2,}")
_CJK_RUN = re.compile(r"[\u4e00-\u9fff]{2,}")


def _date(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _claim_dates(ids: Iterable[str], changes: dict[str, ProductChange]) -> list[datetime]:
    return [
        date for change_id in ids if (date := _date(changes[change_id].published_at)) is not None
    ]


def _validate_temporal_text(text: str, ids: list[str], changes: dict[str, ProductChange]) -> None:
    dates = _claim_dates(ids, changes)
    if _SAME_DAY.search(text):
        if len(ids) < 2 or len(dates) != len(ids) or len({date.date() for date in dates}) != 1:
            raise ValueError("same-day claim contradicts cited Change dates")
    if _SHORT_WINDOW.search(text):
        if len(ids) < 2 or len(dates) != len(ids):
            raise ValueError("short-window claim lacks dated support")
        span = (max(dates) - min(dates)).total_seconds()
        if span > 7 * 24 * 3600:
            raise ValueError("short-window claim exceeds seven days")


def _tokens(value: str) -> set[str]:
    tokens = {match.group(0).casefold() for match in _LATIN.finditer(value)}
    for match in _CJK_RUN.finditer(value):
        run = match.group(0)
        tokens.update(run[index : index + 2] for index in range(len(run) - 1))
    return tokens


def _jaccard(left: set[str], right: set[str]) -> float:
    union = left | right
    return len(left & right) / len(union) if union else 0.0


def _external_ids(changes: dict[str, ProductChange]) -> set[str]:
    return {
        change_id
        for change_id, item in changes.items()
        if item.corpus_role == "external_environment"
    }


def _source_keys(change: ProductChange) -> set[str]:
    return {f"{item.source_type}:{item.source_name}".casefold() for item in change.evidence}


def _validate_direction_support(
    candidate: DirectionCandidate, changes: dict[str, ProductChange]
) -> None:
    supports = list(dict.fromkeys(candidate.supporting_change_ids))
    if len(supports) < 2 or not set(supports) <= _external_ids(changes):
        raise ValueError("EnvironmentDirection may cite only external-environment Changes")
    entities = {changes[change_id].entity.casefold() for change_id in supports}
    sources: set[str] = set()
    for change_id in supports:
        sources.update(_source_keys(changes[change_id]))
    # Two changes from one package/source are a release sequence, not yet an environment direction.
    if len(entities) < 2 and not (len(supports) >= 3 and len(sources) >= 2):
        raise ValueError("EnvironmentDirection lacks independent entity/source diversity")
    _validate_temporal_text(candidate.title, supports, changes)
    _validate_temporal_text(candidate.explanation, supports, changes)
    if _DIRECTION_VELOCITY.search(candidate.title + candidate.explanation):
        raise ValueError("Direction prose must not pre-empt guarded trend state")


def _validate_claim(claim: GroundedClaim, changes: dict[str, ProductChange]) -> None:
    ids = list(dict.fromkeys(claim.supporting_change_ids))
    if not ids or not set(ids) <= _external_ids(changes):
        raise ValueError("Environment report claims may cite only external-environment Changes")
    _validate_temporal_text(claim.text, ids, changes)


def _validate_distinct_directions(candidates: list[DirectionCandidate]) -> None:
    for left, right in combinations(candidates, 2):
        a, b = set(left.supporting_change_ids), set(right.supporting_change_ids)
        overlap = len(a & b) / max(1, min(len(a), len(b)))
        wording = _jaccard(
            _tokens(left.title + " " + left.explanation),
            _tokens(right.title + " " + right.explanation),
        )
        if overlap >= 0.75:
            raise ValueError("Directions reuse almost the same evidence set")
        if overlap >= 0.5 and wording >= 0.10:
            raise ValueError("Directions are semantically overlapping over shared evidence")


def validate_synthesis_semantics(
    output: SynthesisOutput, changes: dict[str, ProductChange]
) -> None:
    """Reject structurally provable semantic defects before a Report can be committed."""
    external = _external_ids(changes)
    if external and not output.brief:
        raise ValueError("Nonempty external corpus requires a grounded brief")
    for claim in output.brief:
        _validate_claim(claim, changes)
    for candidate in output.directions:
        _validate_direction_support(candidate, changes)
    _validate_distinct_directions(output.directions)
    featured = list(dict.fromkeys(output.featured_change_ids))
    if len(featured) != len(output.featured_change_ids) or not set(featured) <= external:
        raise ValueError("Featured must contain unique external Change IDs")
