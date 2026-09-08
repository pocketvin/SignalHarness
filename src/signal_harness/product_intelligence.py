"""Product-facing Scan projections shared by CLI, REST, Web, and MCP adapters."""

from __future__ import annotations

import json
import re
from collections import Counter
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from signal_harness.persistence import ChangeLedger

PRODUCT_PROJECTION_VERSION = "scan-product-v1"
ChangeSort = Literal["rank", "score", "newest"]
MarkdownMode = Literal["report", "top", "change"]

_DECISION_ZH = {
    "action_required": "需要处理",
    "alert": "重点关注",
    "save": "值得保留",
    "ignore": "低优先级",
    None: "待深度分析",
}
_SOURCE_ZH = {
    "github_release": "上游版本发布",
    "github_issue": "上游问题/提案",
    "rss": "技术动态",
    "web_change": "网页变化",
    "package_registry": "包版本变化",
    "team_update": "项目动态",
}


class ProductChangeSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    change_id: str
    rank: int = Field(ge=1)
    change_type: str
    summary_zh: str
    project_impact_zh: str
    decision: str | None = None
    impact_score: float | None = None
    selected_for_analysis: bool
    source_type: str
    source_name: str
    published_at: str | None = None
    evidence_count: int = Field(ge=0)


class ProductChangeDetail(ProductChangeSummary):
    what_changed_zh: str
    why_relevant_zh: str
    affected_modules: list[str] = Field(default_factory=list)
    recommended_actions: list[str] = Field(default_factory=list)
    before_after: dict[str, str] | None = None
    evidence: list[dict[str, str]] = Field(default_factory=list)
    audit: dict[str, Any] = Field(default_factory=dict)


class ProductChangePage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scan_id: str
    items: list[ProductChangeSummary]
    count: int = Field(ge=0)
    all_count: int = Field(ge=0)
    offset: int = Field(ge=0)
    limit: int = Field(ge=1)
    returned: int = Field(ge=0)
    has_more: bool
    sort: ChangeSort


class ProductReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: str = PRODUCT_PROJECTION_VERSION
    scan_id: str
    project_id: str
    profile_revision_id: str | None = None
    coverage_status: str
    window: dict[str, Any]
    summary_zh: str
    stats: dict[str, Any]
    themes: list[str]
    requested_top_count: int = Field(ge=1)
    top_change_ids: list[str]


class ProductScanProjection(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: str = PRODUCT_PROJECTION_VERSION
    scan_id: str
    project_id: str
    report: ProductReport
    top_changes: list[ProductChangeSummary]
    all_changes: ProductChangePage


class ProductIntelligenceService:
    """Read stable product projections from one project's durable ChangeLedger."""

    def __init__(self, *, ledger: ChangeLedger, project_id: str) -> None:
        self.ledger = ledger
        self.project_id = project_id

    def resolve_scan_id(self, scan_id: str | None = None) -> str:
        selected = scan_id or self.ledger.latest_successful_scan_id(project_id=self.project_id)
        if selected is None:
            raise ValueError(f"No successful scan exists for project {self.project_id}")
        metadata = self.ledger.scan_metadata(selected)
        if metadata is None or str(metadata["project_id"]) != self.project_id:
            raise ValueError(f"Unknown scan for project {self.project_id}: {selected}")
        return selected

    def report(self, scan_id: str | None = None, *, top_count: int = 12) -> ProductReport:
        if top_count < 1:
            raise ValueError("top_count must be positive")
        selected = self.resolve_scan_id(scan_id)
        metadata = self._metadata(selected)
        rows = self._all_rows(selected)
        source_counts = Counter(str(row["source_type"]) for row in rows)
        assessments = [row["assessment"] for row in rows if isinstance(row.get("assessment"), dict)]
        decision_counts = Counter(str(item.get("decision") or "unknown") for item in assessments)
        category_counts = Counter(str(item.get("category") or "unknown") for item in assessments)
        analyzed_count = len(assessments)
        high_priority = decision_counts["alert"] + decision_counts["action_required"]
        top = self.top_changes(selected, top_count=top_count)
        themes = [name for name, _ in category_counts.most_common(3)]
        if not themes:
            themes = [name for name, _ in source_counts.most_common(3)]
        source_text = "、".join(name for name, _ in source_counts.most_common(3)) or "暂无来源"
        theme_text = "、".join(themes) or "暂无明确主题"
        coverage = str(metadata.get("coverage_status") or "unknown")
        summary = (
            f"本次扫描冻结了 {len(rows)} 条与项目相关的环境变化，其中 {analyzed_count} 条进入深度影响分析；"
            f"深度分析中有 {high_priority} 条需要优先关注。来源主要包括 {source_text}，"
            f"当前主要主题为 {theme_text}。扫描覆盖状态为 {coverage}；"
            "以下 Top 变化只是阅读优先级，不会改变完整变化集。"
        )
        stats: dict[str, Any] = {
            "all_change_count": len(rows),
            "analyzed_count": analyzed_count,
            "unanalyzed_count": max(0, len(rows) - analyzed_count),
            "high_priority_count": high_priority,
            "source_count": len(source_counts),
            "source_counts": dict(source_counts),
            "decision_counts": dict(decision_counts),
            "category_counts": dict(category_counts),
        }
        return ProductReport(
            scan_id=selected,
            project_id=self.project_id,
            profile_revision_id=(
                str(metadata["profile_revision_id"])
                if metadata.get("profile_revision_id") is not None
                else None
            ),
            coverage_status=coverage,
            window=dict(metadata.get("window") or {}),
            summary_zh=summary,
            stats=stats,
            themes=themes,
            requested_top_count=top_count,
            top_change_ids=[item.change_id for item in top],
        )

    def top_changes(
        self, scan_id: str | None = None, *, top_count: int = 12
    ) -> list[ProductChangeSummary]:
        if top_count < 1:
            raise ValueError("top_count must be positive")
        selected = self.resolve_scan_id(scan_id)
        rows = self._all_rows(selected)
        visible = [
            row
            for row in rows
            if not (
                isinstance(row.get("assessment"), dict)
                and str(row["assessment"].get("decision") or "") == "ignore"
            )
        ]
        ranked = sorted(visible, key=self._top_sort_key)
        return [self._summary(row) for row in ranked[:top_count]]

    def list_changes(
        self,
        scan_id: str | None = None,
        *,
        offset: int = 0,
        limit: int = 20,
        query: str = "",
        decision: str | None = None,
        source_type: str | None = None,
        category: str | None = None,
        analyzed: bool | None = None,
        sort: ChangeSort = "rank",
    ) -> ProductChangePage:
        if offset < 0:
            raise ValueError("offset must be non-negative")
        if limit < 1 or limit > 1000:
            raise ValueError("limit must be between 1 and 1000")
        selected = self.resolve_scan_id(scan_id)
        rows = self._all_rows(selected)
        filtered = [
            row
            for row in rows
            if self._matches(
                row,
                query=query,
                decision=decision,
                source_type=source_type,
                category=category,
                analyzed=analyzed,
            )
        ]
        ordered = self._sort_rows(filtered, sort)
        page_rows = ordered[offset : offset + limit]
        items = [self._summary(row) for row in page_rows]
        return ProductChangePage(
            scan_id=selected,
            items=items,
            count=len(filtered),
            all_count=len(rows),
            offset=offset,
            limit=limit,
            returned=len(items),
            has_more=offset + len(items) < len(filtered),
            sort=sort,
        )

    def change_detail(
        self, change_id: str, *, scan_id: str | None = None
    ) -> ProductChangeDetail:
        selected = self.resolve_scan_id(scan_id)
        row = next(
            (item for item in self._all_rows(selected) if str(item["change_id"]) == change_id),
            None,
        )
        if row is None:
            raise ValueError(f"Unknown change in scan {selected}: {change_id}")
        return self._detail(row)

    def projection(
        self,
        scan_id: str | None = None,
        *,
        top_count: int = 12,
        all_limit: int = 20,
    ) -> ProductScanProjection:
        selected = self.resolve_scan_id(scan_id)
        report = self.report(selected, top_count=top_count)
        return ProductScanProjection(
            scan_id=selected,
            project_id=self.project_id,
            report=report,
            top_changes=self.top_changes(selected, top_count=top_count),
            all_changes=self.list_changes(selected, limit=all_limit),
        )

    def render_markdown(
        self,
        scan_id: str | None = None,
        *,
        mode: MarkdownMode = "report",
        top_count: int = 12,
        change_id: str | None = None,
    ) -> str:
        selected = self.resolve_scan_id(scan_id)
        if mode == "change":
            if not change_id:
                raise ValueError("change Markdown mode requires change_id")
            return self._render_change_markdown(self.change_detail(change_id, scan_id=selected))
        top = self.top_changes(selected, top_count=top_count)
        if mode == "top":
            return self._render_top_markdown(selected, top)
        report = self.report(selected, top_count=top_count)
        return self._render_report_markdown(report, top)

    def _metadata(self, scan_id: str) -> dict[str, Any]:
        metadata = self.ledger.scan_metadata(scan_id)
        if metadata is None:
            raise ValueError(f"Unknown scan: {scan_id}")
        return metadata

    def _all_rows(self, scan_id: str) -> list[dict[str, Any]]:
        first = self.ledger.list_scan_changes(scan_id, offset=0, limit=1)
        if first.count == 0:
            return []
        return self.ledger.list_scan_changes(scan_id, offset=0, limit=first.count).items

    @staticmethod
    def _assessment(row: dict[str, Any]) -> dict[str, Any] | None:
        value = row.get("assessment")
        return value if isinstance(value, dict) else None

    @staticmethod
    def _event_payload(row: dict[str, Any]) -> dict[str, Any]:
        value = row.get("event")
        return dict(value) if isinstance(value, dict) else {}

    @classmethod
    def _summary(cls, row: dict[str, Any]) -> ProductChangeSummary:
        assessment = cls._assessment(row)
        event = cls._event_payload(row)
        category = str(assessment.get("category") or "") if assessment is not None else ""
        decision = str(assessment.get("decision") or "") if assessment is not None else ""
        modules = [str(item) for item in (assessment.get("affected_modules") or [])] if assessment is not None else []
        impact_score = (
            float(assessment["impact_score"])
            if assessment is not None and assessment.get("impact_score") is not None
            else None
        )
        type_name = category or str(row.get("source_type") or "change")
        prefix = _SOURCE_ZH.get(str(row.get("source_type") or ""), "环境变化")
        title = str(row.get("title") or event.get("title") or row.get("change_id"))
        summary_zh = f"{prefix}：{title}"
        decision_zh = _DECISION_ZH.get(decision or None, "待判断")
        if assessment is not None:
            module_text = "、".join(modules) if modules else "项目相关能力"
            score_text = f"，影响分 {impact_score:.1f}" if impact_score is not None else ""
            impact_zh = f"可能影响 {module_text}；当前判定为“{decision_zh}”{score_text}。"
        else:
            impact_zh = "该变化已进入完整变化集，但尚未进入深度影响分析。"
        evidence_urls = assessment.get("evidence_urls") if assessment is not None else None
        evidence_count = len(evidence_urls) if isinstance(evidence_urls, list) else int(bool(row.get("url")))
        return ProductChangeSummary(
            change_id=str(row["change_id"]),
            rank=int(row["rank"]),
            change_type=type_name,
            summary_zh=summary_zh,
            project_impact_zh=impact_zh,
            decision=decision or None,
            impact_score=impact_score,
            selected_for_analysis=bool(row["selected_for_analysis"]),
            source_type=str(row.get("source_type") or "unknown"),
            source_name=str(row.get("source_name") or "unknown"),
            published_at=(str(row["published_at"]) if row.get("published_at") is not None else None),
            evidence_count=evidence_count,
        )

    @classmethod
    def _detail(cls, row: dict[str, Any]) -> ProductChangeDetail:
        summary = cls._summary(row)
        assessment = cls._assessment(row)
        event = cls._event_payload(row)
        content = cls._clean_text(str(event.get("content") or event.get("title") or summary.summary_zh))
        content = content[:1200] + ("…" if len(content) > 1200 else "")
        modules = [str(item) for item in (assessment.get("affected_modules") or [])] if assessment is not None else []
        actions = [str(item) for item in (assessment.get("action_items") or [])] if assessment is not None else []
        if assessment is not None:
            why = summary.project_impact_zh
        else:
            why = "当前只有确定性的项目相关性排序证据，尚未形成深度影响结论。"
        previous = event.get("previous_version")
        current = event.get("current_version")
        before_after = (
            {"before": str(previous), "after": str(current)}
            if previous and current and previous != current
            else None
        )
        urls: list[str] = []
        if assessment is not None and isinstance(assessment.get("evidence_urls"), list):
            urls.extend(str(item) for item in assessment["evidence_urls"] if str(item).strip())
        if row.get("url"):
            urls.append(str(row["url"]))
        urls = list(dict.fromkeys(urls))
        evidence = [
            {
                "url": url,
                "source_name": summary.source_name,
                "source_quality": str(assessment.get("source_quality") or "unverified") if assessment is not None else "unverified",
            }
            for url in urls
        ]
        audit: dict[str, Any] = {
            "rank": int(row["rank"]),
            "basic_relevance_score": float(row["basic_relevance_score"]),
            "selected_for_analysis": bool(row["selected_for_analysis"]),
        }
        if assessment is not None:
            audit.update(
                {
                    "reason": assessment.get("reason"),
                    "confidence": assessment.get("confidence"),
                    "source_quality": assessment.get("source_quality"),
                    "score_breakdown": assessment.get("score_breakdown"),
                    "agent_score_breakdown": assessment.get("agent_score_breakdown"),
                }
            )
        return ProductChangeDetail(
            **summary.model_dump(),
            what_changed_zh=content or summary.summary_zh,
            why_relevant_zh=why,
            affected_modules=modules,
            recommended_actions=actions,
            before_after=before_after,
            evidence=evidence,
            audit=audit,
        )

    @classmethod
    def _matches(
        cls,
        row: dict[str, Any],
        *,
        query: str,
        decision: str | None,
        source_type: str | None,
        category: str | None,
        analyzed: bool | None,
    ) -> bool:
        assessment = cls._assessment(row)
        if decision is not None and str((assessment or {}).get("decision") or "") != decision:
            return False
        if source_type is not None and str(row.get("source_type") or "") != source_type:
            return False
        if category is not None and str((assessment or {}).get("category") or "") != category:
            return False
        if analyzed is not None and bool(row.get("selected_for_analysis")) is not analyzed:
            return False
        needle = query.strip().lower()
        if needle:
            searchable = json.dumps(
                {
                    "title": row.get("title"),
                    "source_type": row.get("source_type"),
                    "source_name": row.get("source_name"),
                    "event": row.get("event"),
                    "assessment": assessment,
                },
                ensure_ascii=False,
                sort_keys=True,
                default=str,
            ).lower()
            if needle not in searchable:
                return False
        return True

    @classmethod
    def _sort_rows(cls, rows: list[dict[str, Any]], sort: ChangeSort) -> list[dict[str, Any]]:
        if sort == "rank":
            return sorted(rows, key=lambda row: (int(row["rank"]), str(row["change_id"])))
        if sort == "score":
            return sorted(
                rows,
                key=lambda row: (-cls._row_score(row), int(row["rank"]), str(row["change_id"])),
            )
        if sort == "newest":
            return sorted(
                rows,
                key=lambda row: (-cls._published_timestamp(row), int(row["rank"]), str(row["change_id"])),
            )
        raise ValueError(f"Unknown sort mode: {sort}")

    @classmethod
    def _top_sort_key(cls, row: dict[str, Any]) -> tuple[int, float, int, str]:
        assessment = cls._assessment(row)
        decision = str(assessment.get("decision") or "") if assessment is not None else ""
        priority = {"action_required": 4, "alert": 3, "save": 2, "": 1, "ignore": 0}.get(decision, 1)
        return (-priority, -cls._row_score(row), int(row["rank"]), str(row["change_id"]))

    @classmethod
    def _row_score(cls, row: dict[str, Any]) -> float:
        assessment = cls._assessment(row)
        if assessment and assessment.get("impact_score") is not None:
            return float(assessment["impact_score"])
        return float(row.get("basic_relevance_score") or 0.0)

    @staticmethod
    def _published_timestamp(row: dict[str, Any]) -> float:
        value = row.get("published_at")
        if not value:
            return 0.0
        raw = str(value)
        if raw.endswith(("Z", "z")):
            raw = raw[:-1] + "+00:00"
        try:
            return datetime.fromisoformat(raw).timestamp()
        except ValueError:
            return 0.0

    @staticmethod
    def _clean_text(value: str) -> str:
        text = re.sub(r"```[\s\S]*?```", " ", value)
        text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)
        text = re.sub(r"[`*_>#]", " ", text)
        return " ".join(text.split()).strip()

    @staticmethod
    def _render_top_markdown(scan_id: str, items: list[ProductChangeSummary]) -> str:
        lines = [f"# SignalHarness Top Changes — {scan_id}", ""]
        if not items:
            return "\n".join([*lines, "_当前没有可展示的重点变化。", ""])
        for index, item in enumerate(items, start=1):
            lines.extend(
                [
                    f"## {index}. {item.summary_zh}",
                    "",
                    f"- 类型：{item.change_type}",
                    f"- 项目影响：{item.project_impact_zh}",
                    f"- 来源：{item.source_name}",
                    f"- Change ID：`{item.change_id}`",
                    "",
                ]
            )
        return "\n".join(lines).rstrip() + "\n"

    @classmethod
    def _render_report_markdown(
        cls, report: ProductReport, top: list[ProductChangeSummary]
    ) -> str:
        stats = report.stats
        lines = [
            f"# SignalHarness 项目环境报告 — {report.scan_id}",
            "",
            report.summary_zh,
            "",
            "## 扫描概览",
            "",
            f"- 完整变化：{stats['all_change_count']}",
            f"- 深度分析：{stats['analyzed_count']}",
            f"- 高优先级：{stats['high_priority_count']}",
            f"- 覆盖状态：{report.coverage_status}",
            f"- Profile Revision：`{report.profile_revision_id or 'unknown'}`",
            "",
            "## 重点变化",
            "",
        ]
        top_body = cls._render_top_markdown(report.scan_id, top).splitlines()[2:]
        lines.extend(top_body)
        return "\n".join(lines).rstrip() + "\n"

    @staticmethod
    def _render_change_markdown(item: ProductChangeDetail) -> str:
        lines = [
            f"# {item.summary_zh}",
            "",
            f"- Change ID：`{item.change_id}`",
            f"- 类型：{item.change_type}",
            f"- 来源：{item.source_name}",
            f"- 项目影响：{item.project_impact_zh}",
            "",
            "## 发生了什么",
            "",
            item.what_changed_zh,
            "",
            "## 为什么与项目相关",
            "",
            item.why_relevant_zh,
            "",
        ]
        if item.before_after:
            lines.extend(
                [
                    "## Before / After",
                    "",
                    f"- Before：{item.before_after['before']}",
                    f"- After：{item.before_after['after']}",
                    "",
                ]
            )
        lines.extend(["## 建议动作", ""])
        lines.extend(
            [f"- {action}" for action in item.recommended_actions]
            or ["- 暂无需要执行的动作。"]
        )
        lines.extend(["", "## 证据", ""])
        lines.extend([f"- {evidence['url']}" for evidence in item.evidence] or ["- 暂无外部链接。"])
        return "\n".join(lines).rstrip() + "\n"
