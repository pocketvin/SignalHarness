"""Compact project references for shallow semantic projection.

The weak model chooses at most one stable project reference plus a short relation note. Python
renders the final user-facing relation reason, so every Change does not restate the whole profile.
"""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any, Literal

from signal_harness.intelligence.contracts import ChangeDigest, ShallowInsight, ShallowModelRow

ProjectRelation = Literal["direct", "context", "none", "unknown"]
ReferenceKind = Literal["dependency", "protocol", "runtime", "provider", "module", "ecosystem"]

_KIND_PREFIX: dict[ReferenceKind, str] = {
    "dependency": "d",
    "protocol": "p",
    "runtime": "r",
    "provider": "v",
    "module": "m",
    "ecosystem": "e",
}
_KIND_LABEL: dict[ReferenceKind, str] = {
    "dependency": "依赖",
    "protocol": "协议",
    "runtime": "运行环境",
    "provider": "外部服务",
    "module": "关注能力",
    "ecosystem": "关注生态",
}


@dataclass(frozen=True)
class ProjectReference:
    ref_id: str
    kind: ReferenceKind
    label: str


def _values(profile: dict[str, Any], key: str) -> list[str]:
    raw = profile.get(key, [])
    if not isinstance(raw, list):
        return []
    values: list[str] = []
    for item in raw:
        if isinstance(item, dict):
            item = item.get("name") or item.get("id") or item.get("repo") or ""
        text = str(item).strip()
        if text:
            values.append(text)
    return values


def build_project_references(profile: dict[str, Any]) -> list[ProjectReference]:
    """Stable, de-duplicated refs ordered from concrete exposure to broad context."""
    groups: tuple[tuple[ReferenceKind, str], ...] = (
        ("dependency", "dependencies"),
        ("protocol", "protocols"),
        ("runtime", "runtimes"),
        ("provider", "providers"),
        ("module", "critical_modules"),
        ("ecosystem", "monitored_ecosystem"),
    )
    seen: set[str] = set()
    counters: dict[ReferenceKind, int] = {kind: 0 for kind, _ in groups}
    refs: list[ProjectReference] = []
    for kind, key in groups:
        for label in _values(profile, key):
            normalized = label.casefold().strip()
            if normalized in seen:
                continue
            seen.add(normalized)
            counters[kind] += 1
            refs.append(
                ProjectReference(
                    ref_id=f"{_KIND_PREFIX[kind]}{counters[kind]}",
                    kind=kind,
                    label=label,
                )
            )
    return refs


def shallow_project_context(profile: dict[str, Any]) -> dict[str, Any]:
    """Compact wire context used only by ChangeInterpreter batches."""
    refs = build_project_references(profile)
    goal = str(profile.get("purpose") or profile.get("goal") or "").strip()
    preferences = []
    for item in profile.get("importance_preferences", []):
        if not isinstance(item, dict) or not item.get("scope_key"):
            continue
        preferences.append(
            [
                str(item.get("scope_type") or "topic")[:16],
                str(item.get("scope_key"))[:80],
                str(item.get("importance") or "normal")[:12],
            ]
        )
    groups: dict[str, list[str]] = {}
    for item in refs:
        groups.setdefault(item.ref_id[0], []).append(item.label)
    return {
        "g": goal[:180],
        "r": groups,
        "q": preferences[:24],
    }


def project_reference_map(profile: dict[str, Any]) -> dict[str, ProjectReference]:
    return {item.ref_id: item for item in build_project_references(profile)}


def _match_text(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", value.casefold()).strip()


def validate_model_projection(
    row: ShallowModelRow,
    *,
    digest: ChangeDigest,
    known_relation: ProjectRelation,
    references: dict[str, ProjectReference],
) -> None:
    """Keep relation authority deterministic when source/profile facts are concrete."""
    if known_relation in {"direct", "context"} and row.r != known_relation:
        raise ValueError("project_relation_basis_deterministic_mismatch")
    if row.r in {"none", "unknown"}:
        if row.b:
            raise ValueError("project_relation_basis_must_be_empty")
        return
    reference = references.get(row.b)
    if reference is None:
        raise ValueError("project_relation_basis_reference_invalid")
    if "signalharness" in row.n.casefold() or reference.label.casefold() in row.n.casefold():
        raise ValueError("project_relation_basis_note_repeats_context")

    entity = _match_text(digest.entity)
    label = _match_text(reference.label)
    change_text = _match_text(
        " ".join([digest.entity, digest.title, *(item.excerpt for item in digest.evidence)])
    )
    concrete = {"dependency", "protocol", "runtime", "provider"}
    if row.r == "direct":
        if reference.kind not in concrete or not label or label != entity:
            raise ValueError("project_relation_basis_direct_not_exact_exposure")
    elif reference.kind in concrete:
        tokens = [token for token in label.split() if len(token) >= 3]
        if not tokens or not all(token in change_text for token in tokens):
            raise ValueError("project_relation_basis_concrete_not_in_change")


def reference_id_for_basis(
    references: dict[str, ProjectReference], *, kind: str, label: str
) -> str:
    target = _match_text(label)
    for ref_id, reference in references.items():
        if reference.kind == kind and _match_text(reference.label) == target:
            return ref_id
    return ""


def _clean_note(note: str, reference: ProjectReference | None = None) -> str:
    value = " ".join(note.split()).strip("；。 ,，")
    value = re.sub(r"SignalHarness", "项目", value, flags=re.IGNORECASE)
    if reference and reference.label:
        value = re.sub(re.escape(reference.label), "", value, flags=re.IGNORECASE)
    value = re.sub(r"\s{2,}", " ", value).strip("；。 ,，")
    return value


def resolve_model_projection(
    row: ShallowModelRow,
    *,
    digest: ChangeDigest,
    known_relation: ProjectRelation,
    known_basis_kind: str,
    known_basis_label: str,
    references: dict[str, ProjectReference],
) -> tuple[ProjectRelation, str, str]:
    """Resolve one model suggestion without turning a local relation error into batch repair."""
    known_basis_id = (
        reference_id_for_basis(
            references,
            kind=known_basis_kind,
            label=known_basis_label,
        )
        if known_basis_kind and known_basis_label
        else ""
    )
    if known_relation in {"direct", "context"} and known_basis_id:
        known_reference = references[known_basis_id]
        return known_relation, known_basis_id, _clean_note(row.n, known_reference)

    if row.r in {"none", "unknown"}:
        return row.r, "", _clean_note(row.n)

    reference = references.get(row.b)
    if reference is None:
        return "unknown", "", ""

    entity = _match_text(digest.entity)
    label = _match_text(reference.label)
    change_text = _match_text(
        " ".join([digest.entity, digest.title, *(item.excerpt for item in digest.evidence)])
    )
    concrete = {"dependency", "protocol", "runtime", "provider"}
    note = _clean_note(row.n, reference)

    if row.r == "direct":
        if reference.kind in concrete and label and label == entity:
            return "direct", row.b, note
        if reference.kind in {"module", "ecosystem"}:
            return "context", row.b, note
        tokens = [token for token in label.split() if len(token) >= 3]
        if reference.kind in concrete and tokens and all(token in change_text for token in tokens):
            return "context", row.b, note
        return "unknown", "", ""

    if reference.kind in {"module", "ecosystem"}:
        return "context", row.b, note
    tokens = [token for token in label.split() if len(token) >= 3]
    if reference.kind in concrete and tokens and all(token in change_text for token in tokens):
        return "context", row.b, note
    return "unknown", "", ""


def render_relation_reason(
    *,
    relation: ProjectRelation,
    basis_id: str,
    note: str,
    references: dict[str, ProjectReference],
) -> str:
    """Render one concise user-facing reason and validate model-selected reference ownership."""
    basis_id = basis_id.strip()
    note = " ".join(note.split()).strip("；。 ")
    if relation in {"direct", "context"}:
        if not basis_id or basis_id not in references:
            raise ValueError("project_relation_basis_reference_invalid")
        reference = references[basis_id]
        prefix = f"{_KIND_LABEL[reference.kind]}：{reference.label}"
        return f"{prefix}；{note}。" if note else f"{prefix}。"
    if basis_id:
        raise ValueError("project_relation_basis_must_be_empty")
    if note:
        return note + "。"
    return (
        "现有项目画像不足以确认关联。"
        if relation == "unknown"
        else "未发现与当前项目的明确技术关联。"
    )


def model_row_to_insight(
    row: ShallowModelRow,
    *,
    references: dict[str, ProjectReference],
    digest: ChangeDigest | None = None,
    known_relation: ProjectRelation = "unknown",
    known_basis_kind: str = "",
    known_basis_label: str = "",
) -> ShallowInsight:
    """Convert compact provider output into the stable cached/product contract."""
    relation: ProjectRelation = row.r
    basis_id = row.b
    note = row.n
    if digest is not None:
        relation, basis_id, note = resolve_model_projection(
            row,
            digest=digest,
            known_relation=known_relation,
            known_basis_kind=known_basis_kind,
            known_basis_label=known_basis_label,
            references=references,
        )
    reason = render_relation_reason(
        relation=relation,
        basis_id=basis_id,
        note=note,
        references=references,
    )
    return ShallowInsight(
        change_id=row.id,
        summary=row.s,
        what_changed=row.f,
        project_relation=relation,
        relation_reason=reason,
        attention=row.a,
        topics=row.t,
        evidence_ids=row.e,
        uncertainty=row.u,
    )
