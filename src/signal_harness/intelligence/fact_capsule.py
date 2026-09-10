"""Deterministic facts extracted before deciding whether a Change needs an LLM."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Literal

from packaging.utils import canonicalize_name

from signal_harness.intelligence.contracts import ChangeDigest, ShallowInsight

Relation = Literal["direct", "context", "none", "unknown"]


@dataclass(frozen=True)
class FactCapsule:
    change_id: str
    revision_id: str
    entity: str
    kind: str
    published_at: str | None
    current_version: str | None
    corpus_role: str
    source_posture: str
    primary_authority: str
    source_names: tuple[str, ...]
    evidence_ids: tuple[str, ...]
    title: str
    primary_excerpt: str
    deterministic_relation: Relation
    deterministic_relation_reason: str
    deterministic_eligible: bool
    deterministic_topics: tuple[str, ...]

    def semantic_payload(
        self, *, primary_chars: int = 760, secondary_chars: int = 260
    ) -> dict[str, Any]:
        """Compact one model task; evidence remains bound to this Change only."""
        # The caller adds evidence snippets from the digest through capsule_semantic_payload().
        return {
            "change_id": self.change_id,
            "entity": self.entity,
            "kind": self.kind,
            "date": self.published_at,
            "version": self.current_version,
            "corpus_role": self.corpus_role,
            "source_posture": self.source_posture,
            "primary_authority": self.primary_authority,
            "title": self.title,
            "known_project_relation": self.deterministic_relation,
            "known_project_relation_reason": self.deterministic_relation_reason,
        }


_AUTHORITY_ORDER = {"official": 5, "maintainer": 4, "secondary": 3, "community": 2, "unverified": 1}


def _norm(value: str) -> str:
    value = value.strip().casefold()
    package = canonicalize_name(value)
    return re.sub(r"[^a-z0-9./+-]+", "-", package).strip("-")


def _profile_terms(profile: dict[str, Any], *keys: str) -> set[str]:
    values: list[Any] = []
    for key in keys:
        raw = profile.get(key, [])
        if isinstance(raw, list):
            values.extend(raw)
    result: set[str] = set()
    for value in values:
        if isinstance(value, dict):
            value = value.get("name") or value.get("id") or value.get("repo") or ""
        text = str(value).strip()
        if text:
            result.add(_norm(text))
    return result


def _source_posture(digest: ChangeDigest) -> str:
    kinds = {item.source_type for item in digest.evidence}
    if kinds == {"github_issue"}:
        return "reported_issue"
    if "github_issue" in kinds:
        return "mixed"
    return "observed_change"


def _exact_relation(digest: ChangeDigest, profile: dict[str, Any]) -> tuple[Relation, str]:
    if digest.corpus_role == "project_activity":
        return "direct", "这是当前项目自身的代码或发布活动。"
    entity = _norm(digest.entity)
    dependencies = _profile_terms(profile, "dependencies", "dependency_evidence")
    protocols = _profile_terms(profile, "protocols")
    providers = _profile_terms(profile, "providers")
    ecosystems = _profile_terms(profile, "monitored_ecosystem")
    if entity and entity in dependencies:
        return "direct", f"项目当前直接使用 {digest.entity}。"
    if entity and entity in protocols:
        return "direct", f"项目当前直接使用 {digest.entity} 协议。"
    if entity and entity in providers:
        return "direct", f"项目当前直接接入 {digest.entity}。"
    if entity and entity in ecosystems:
        return "context", f"{digest.entity} 属于项目明确关注的外部生态。"
    if digest.kind == "security_advisory" and digest.entity not in {"OSV", "unknown-package"}:
        # OSV collection is created only from the project's resolved dependency set.
        return "direct", f"该安全公告来自项目已解析依赖 {digest.entity} 的匹配结果。"
    return "unknown", ""


def _deterministic_topics(digest: ChangeDigest) -> tuple[str, ...]:
    if digest.corpus_role == "project_activity":
        return ("项目自身变化",)
    if digest.kind in {"package_registry", "github_release"}:
        return ("版本发布",)
    if digest.kind == "security_advisory":
        return ("安全公告",)
    return ()


def build_fact_capsule(digest: ChangeDigest, profile: dict[str, Any]) -> FactCapsule:
    relation, reason = _exact_relation(digest, profile)
    primary = max(
        digest.evidence,
        key=lambda item: (_AUTHORITY_ORDER.get(item.authority, 0), -item.event_revision_id),
    )
    # Conservative bypass: package-index releases and project-owned activity are sufficiently
    # structured for a lightweight fact-level insight. Rich GitHub releases, advisories,
    # issues, RSS and web diffs still need semantic interpretation.
    abnormal_package_metadata = bool(
        re.search(r"\b[1-9][0-9]*\s+yanked\b", primary.excerpt, flags=re.IGNORECASE)
    )
    structured_fact = digest.corpus_role == "project_activity" or (
        digest.kind == "package_registry"
        and all(item.source_type == "package_registry" for item in digest.evidence)
        and bool(digest.current_version)
        and primary.authority in {"official", "maintainer"}
        and not abnormal_package_metadata
    )
    return FactCapsule(
        change_id=digest.change_id,
        revision_id=digest.revision_id,
        entity=digest.entity,
        kind=digest.kind,
        published_at=digest.published_at,
        current_version=digest.current_version,
        corpus_role=digest.corpus_role,
        source_posture=_source_posture(digest),
        primary_authority=primary.authority,
        source_names=tuple(dict.fromkeys(item.source_name for item in digest.evidence)),
        evidence_ids=tuple(item.evidence_id for item in digest.evidence),
        title=digest.title,
        primary_excerpt=primary.excerpt,
        deterministic_relation=relation,
        deterministic_relation_reason=reason,
        deterministic_eligible=structured_fact and relation != "unknown",
        deterministic_topics=_deterministic_topics(digest),
    )


def deterministic_insight(capsule: FactCapsule) -> ShallowInsight:
    if not capsule.deterministic_eligible:
        raise ValueError("FactCapsule is not deterministic-insight eligible")
    version = capsule.current_version or ""
    if capsule.corpus_role == "project_activity":
        summary = f"项目自身更新：{capsule.title}"
        what_changed = f"项目仓库记录了新的代码提交或合并活动，原始标题为“{capsule.title}”。"
        uncertainty = "这是项目自身活动，只作为项目背景，不用于证明外部环境方向。"
    elif capsule.kind == "security_advisory":
        summary = f"{capsule.entity} 出现与当前依赖匹配的安全公告"
        what_changed = (
            f"安全公告匹配到项目当前使用的 {capsule.entity}{f' {version}' if version else ''}。"
        )
        uncertainty = "这里只确认公告与依赖版本匹配；是否存在实际可达风险需要按需深入核实。"
    else:
        summary = f"{capsule.entity} 发布版本 {version}".strip()
        what_changed = f"官方或维护者来源记录了 {capsule.entity} 版本 {version} 的发布。"
        uncertainty = "这里只确认版本发布事实；具体行为变化需要读取发布说明后再判断。"
    return ShallowInsight(
        change_id=capsule.change_id,
        summary=summary,
        what_changed=what_changed,
        project_relation=capsule.deterministic_relation,
        relation_reason=capsule.deterministic_relation_reason,
        attention="watch" if capsule.kind == "security_advisory" else "normal",
        topics=list(capsule.deterministic_topics),
        evidence_ids=list(capsule.evidence_ids),
        uncertainty=uncertainty,
    )


def capsule_semantic_payload(
    capsule: FactCapsule,
    digest: ChangeDigest,
    *,
    primary_chars: int = 760,
    secondary_chars: int = 260,
) -> dict[str, Any]:
    payload = capsule.semantic_payload()
    primary_id = max(
        digest.evidence,
        key=lambda item: (_AUTHORITY_ORDER.get(item.authority, 0), -item.event_revision_id),
    ).evidence_id
    evidence = []
    for item in digest.evidence:
        limit = primary_chars if item.evidence_id == primary_id else secondary_chars
        evidence.append(
            {
                "evidence_id": item.evidence_id,
                "source": item.source_name,
                "authority": item.authority,
                "excerpt": item.excerpt[:limit],
                "truncated": item.excerpt_truncated or len(item.excerpt) > limit,
            }
        )
    payload["evidence"] = evidence
    return payload
