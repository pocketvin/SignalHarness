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
from signal_harness.intelligence.direction_identity import (
    canonical_entity_family,
    independent_source_channel,
)

_SAME_DAY = re.compile(r"同日|同一天|同一日|当天")
_SHORT_WINDOW = re.compile(r"短时间内|短期内")
# Direction velocity belongs to the guarded Direction.state computed from comparable scans.
_DIRECTION_VELOCITY = re.compile(
    r"加速|减速|增强|减弱|走强|走弱|升温|降温|激增|骤降|明显增长|显著增长|明显下降|显著下降|翻倍|同比|环比"
)
_RADAR_TREND_CLAIM = re.compile(r"风口|趋势|升温|爆发|普及|席卷|成为主流|加速采用")
# Repository discovery can prove that independent implementations are appearing. It does not
# measure adoption, market share, or industry consensus. Keep this separate from generic trend
# language so an emerging_direction may still say "开始形成", while claims such as "标配" or
# "普遍采用" remain impossible to infer from search results alone.
_DISCOVERY_ADOPTION_CLAIM = re.compile(
    r"标配|普遍|广泛(?:采用|复刻|使用|部署|支持|出现)|成为(?:行业)?(?:标准|主流|标配)|"
    r"行业标准|事实标准|基本盘|普及"
)
_LATIN = re.compile(r"[A-Za-z][A-Za-z0-9_.+/-]{2,}")
_CJK_RUN = re.compile(r"[\u4e00-\u9fff]{2,}")
_PROJECT_COPY_TEMPLATE = re.compile(
    r"^(?:本项目|当前项目|项目)(?:的|中|里|目前|现在|直接|正在|依赖|使用|关注|重点)?"
)
_REPORTY_DIRECTION_TITLE = re.compile(
    r"讨论|被讨论|调研|信号增多|问题浮现|变化集中出现|问题集中出现|共同议题|同时出现"
)
_BRIEF_SOURCE_JARGON = re.compile(r"(?i)(?:github\s*)?issue|\bRSS\b|来源清单|引用.{0,8}(?:作者|博客)")


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
    return {independent_source_channel(item) for item in change.evidence}


def _lacks_source_diversity(
    candidate: DirectionCandidate, changes: dict[str, ProductChange]
) -> bool:
    """Return True only for the narrow, deterministic diversity failure class.

    Invalid citations and duplicate-only support remain normal validation failures; they are not
    silently discarded and still use the bounded repair path.
    """
    supports = list(dict.fromkeys(candidate.supporting_change_ids))
    if len(supports) < 2 or not set(supports) <= _external_ids(changes):
        return False
    entities = {
        canonical_entity_family(changes[change_id].entity) for change_id in supports
    }
    sources: set[str] = set()
    for change_id in supports:
        sources.update(_source_keys(changes[change_id]))
    return len(entities) < 2 and not (len(supports) >= 3 and len(sources) >= 2)


def prune_source_diversity_candidates(
    output: SynthesisOutput, changes: dict[str, ProductChange]
) -> tuple[SynthesisOutput, int]:
    """Drop only structurally unsupported Direction candidates before global validation.

    One weak candidate must not invalidate a grounded brief/Featured projection and force a full
    strong-model rerun. All other semantic, citation, temporal, and overlap guards still run.
    """
    kept = [item for item in output.directions if not _lacks_source_diversity(item, changes)]
    dropped = len(output.directions) - len(kept)
    return output.model_copy(update={"directions": kept}), dropped


def _validate_direction_support(
    candidate: DirectionCandidate, changes: dict[str, ProductChange]
) -> None:
    supports = list(dict.fromkeys(candidate.supporting_change_ids))
    if len(supports) < 2 or not set(supports) <= _external_ids(changes):
        raise ValueError("EnvironmentDirection may cite only external-environment Changes")
    # Two changes from one package/source are a release sequence, not yet an environment direction.
    if _lacks_source_diversity(candidate, changes):
        raise ValueError("EnvironmentDirection lacks independent entity/source diversity")
    _validate_temporal_text(candidate.title, supports, changes)
    _validate_temporal_text(candidate.explanation, supports, changes)
    if _DIRECTION_VELOCITY.search(candidate.title + candidate.explanation):
        raise ValueError("Direction prose must not pre-empt guarded trend state")


def _validate_claim(claim: GroundedClaim, changes: dict[str, ProductChange]) -> None:
    ids = list(dict.fromkeys(claim.supporting_change_ids))
    if not ids or not set(ids) <= _external_ids(changes):
        raise ValueError("Environment report claims may cite only external-environment Changes")
    if len(ids) == 1 and _RADAR_TREND_CLAIM.search(claim.text):
        raise ValueError("Single Change brief cannot claim a market trend")
    if all(changes[change_id].discovery_origin == "discovered" for change_id in ids) and (
        _DISCOVERY_ADOPTION_CLAIM.search(claim.text)
    ):
        raise ValueError("Discovery-only brief cannot claim adoption prevalence")
    _validate_temporal_text(claim.text, ids, changes)


def _validate_radar(output: SynthesisOutput, changes: dict[str, ProductChange]) -> None:
    external = _external_ids(changes)
    discovered = {
        change_id
        for change_id, item in changes.items()
        if item.corpus_role == "external_environment" and item.discovery_origin == "discovered"
    }
    if output.radar and not discovered:
        raise ValueError("Radar requires project-conditioned discovered Changes")
    for item in output.radar:
        ids = list(dict.fromkeys(item.supporting_change_ids))
        if len(ids) != len(item.supporting_change_ids) or not set(ids) <= external:
            raise ValueError("Radar may cite only unique external-environment Changes")
        discovered_support = [change_id for change_id in ids if change_id in discovered]
        if not discovered_support:
            raise ValueError("Radar must include at least one discovered Change")
        if not item.project_connection.strip():
            raise ValueError("Radar must explain the project connection")
        if _DISCOVERY_ADOPTION_CLAIM.search(item.title + item.explanation + item.why_now):
            raise ValueError("Radar discovery evidence cannot claim adoption prevalence")
        if item.radar_type == "new_solution":
            if _RADAR_TREND_CLAIM.search(item.title + item.explanation + item.why_now):
                raise ValueError("A single new solution must not be described as a market trend")
        else:
            entities = {
                canonical_entity_family(changes[change_id].entity)
                for change_id in discovered_support
            }
            sources = {
                source
                for change_id in discovered_support
                for source in _source_keys(changes[change_id])
            }
            if len(discovered_support) < 2 or len(entities) < 2 or len(sources) < 2:
                raise ValueError(
                    "Emerging radar direction requires at least two independent discovered entities"
                )
        _validate_temporal_text(item.explanation, ids, changes)


def _validate_product_copy_style(output: SynthesisOutput) -> None:
    """Reject deterministic report-copy patterns that make the product read like a lab report."""
    if len(output.brief) > 3:
        raise ValueError("human_product_copy brief must contain at most three overall judgments")
    if any(_BRIEF_SOURCE_JARGON.search(claim.text) for claim in output.brief):
        raise ValueError("human_product_copy brief must summarize meaning instead of source mechanics")
    for candidate in output.directions:
        if _REPORTY_DIRECTION_TITLE.search(candidate.title.strip()):
            raise ValueError("human_product_copy direction title is report-like rather than a takeaway")
        if candidate.project_connection and _PROJECT_COPY_TEMPLATE.search(
            candidate.project_connection.strip()
        ):
            raise ValueError("human_product_copy project connection repeats project-profile boilerplate")
        if len(candidate.watch_next) > 2:
            raise ValueError("human_product_copy watch-next must stay focused on at most two triggers")


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
    _validate_product_copy_style(output)
    for claim in output.brief:
        _validate_claim(claim, changes)
    for candidate in output.directions:
        _validate_direction_support(candidate, changes)
    _validate_distinct_directions(output.directions)
    _validate_radar(output, changes)
    featured = list(dict.fromkeys(output.featured_change_ids))
    if len(featured) != len(output.featured_change_ids) or not set(featured) <= external:
        raise ValueError("Featured must contain unique external Change IDs")
