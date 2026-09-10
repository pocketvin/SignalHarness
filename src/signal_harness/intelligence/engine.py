"""Full-corpus shallow interpretation followed by ONE global synthesis call."""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

from signal_harness.intelligence.contracts import (
    CHANGE_INSIGHT_VERSION,
    INTELLIGENCE_VERSION,
    ChangeDigest,
    EnvironmentReport,
    InsightBatch,
    ProductChange,
    Progress,
    ShallowInsight,
    SynthesisOutput,
)
from signal_harness.intelligence.corpus import (
    compact_digest,
    corpus_payload,
    finalize_direction,
    identity,
    project_change,
)
from signal_harness.intelligence.model_calls import BoundedModelCaller
from signal_harness.intelligence.quality import validate_synthesis_semantics
from signal_harness.persistence.intelligence import IntelligenceRepository, utc_now

ProgressListener = Callable[[Progress], None]


def project_context(profile: dict[str, Any]) -> dict[str, Any]:
    """Expose project meaning to models without leaking backend field vocabulary into prose."""
    importance_labels = {
        "critical": "重点关注",
        "important": "重要",
        "normal": "正常",
        "low": "降低关注",
        "ignore": "忽略",
    }
    scope_labels = {
        "dependency": "依赖",
        "provider": "外部服务",
        "runtime": "运行环境",
        "protocol": "协议",
        "module": "模块",
        "ecosystem": "生态",
        "source": "来源",
        "category": "类别",
        "topic": "主题",
    }
    preferences = [
        {
            "对象类型": scope_labels.get(str(item.get("scope_type")), "主题"),
            "对象": str(item.get("scope_key") or ""),
            "关注程度": importance_labels.get(str(item.get("importance")), "正常"),
        }
        for item in profile.get("importance_preferences", [])
        if item.get("scope_key")
    ]
    return {
        "项目名称": profile.get("project_name"),
        "用途": profile.get("purpose") or profile.get("goal"),
        "主要技术": profile.get("tech_stack", []),
        "运行环境": profile.get("runtimes", []),
        "使用的依赖": profile.get("dependencies", []),
        "外部服务": profile.get("providers", []),
        "协议": profile.get("protocols", []),
        "重点模块": profile.get("critical_modules", []),
        "关注生态": profile.get("monitored_ecosystem", []),
        "用户明确关注": preferences,
    }


class EnvironmentEngine:
    def __init__(
        self,
        repository: IntelligenceRepository,
        caller: BoundedModelCaller,
        progress: ProgressListener | None = None,
    ) -> None:
        self.repository, self.caller = repository, caller
        self.listener = progress

    def progress(
        self, stage: str, message: str, completed: int = 0, total: int | None = None, **kwargs: Any
    ) -> None:
        if self.listener:
            self.listener(
                Progress(stage=stage, message=message, completed=completed, total=total, **kwargs)
            )

    async def run(
        self,
        *,
        scan_id: str,
        project_id: str,
        profile_revision_id: str,
        profile: dict[str, Any],
        digests: list[ChangeDigest],
        window: dict[str, Any],
        observed_count: int,
        sources: list[dict[str, Any]],
        coverage_status: str,
        data_origin: str = "live",
    ) -> EnvironmentReport:
        existing = self.repository.report(scan_id)
        if existing:
            # Restart after report commit: do not replay billable model calls.
            return EnvironmentReport.model_validate(existing)
        self.repository.freeze(scan_id, digests)
        changes, cache_hits = await self._interpret(scan_id, profile_revision_id, profile, digests)
        previous = (
            self.repository.latest_report(project_id, before=str(window["from"]))
            if window.get("from")
            else None
        )
        external_changes = [item for item in changes if item.corpus_role == "external_environment"]
        project_activity = [item for item in changes if item.corpus_role == "project_activity"]
        payload = {
            "window": window,
            "project": project_context(profile),
            "coverage_status": coverage_status,
            "sources": sources,
            "corpus": corpus_payload(external_changes),
            "project_activity": corpus_payload(project_activity),
            "previous_report": {
                key: previous[key] for key in ("window", "directions", "coverage_status")
            }
            if previous
            else None,
            "manifest": {
                "external_change_ids": [c.change_id for c in external_changes],
                "project_activity_ids": [c.change_id for c in project_activity],
                "external_count": len(external_changes),
                "project_activity_count": len(project_activity),
            },
        }
        by_id = {item.change_id: item for item in changes}
        notices: list[str] = []
        if data_origin == "replay":
            notices.append("资料回放验证：本报告基于已保存来源，不是本期完整实时采集。")
        failed = sum(c.interpretation_status != "ready" for c in changes)
        if failed:
            notices.append(
                f"{failed} 条变化的自动解释未完成；原始证据仍可查看，不能据此判断它们没有影响。"
            )
        if coverage_status != "complete":
            notices.append(
                "部分来源未完整覆盖本期；报告只基于已取得的资料，不代表外部世界的全部变化。"
            )
        self.progress("forming_directions", "正在把全部变化综合为本期方向", 0, len(changes))
        directions = []
        synthesis = SynthesisOutput()
        synthesis_ok = not external_changes
        if external_changes:
            try:
                encoded_size = len(json.dumps(payload, ensure_ascii=False).encode())
                if encoded_size > self.caller.policy.global_input_bytes:
                    raise ValueError("global_context_budget_exceeded")

                def validate(output: SynthesisOutput) -> None:
                    validate_synthesis_semantics(output, by_id)
                    seen: set[str] = set()
                    for candidate in output.directions:
                        direction = finalize_direction(
                            candidate,
                            changes=by_id,
                            project_id=project_id,
                            scan_id=scan_id,
                            previous=previous,
                            window=window,
                            coverage=coverage_status,
                            sources=sources,
                        )
                        if direction.direction_id in seen:
                            raise ValueError("Duplicate direction identities")
                        seen.add(direction.direction_id)

                synthesis = await self.caller.complete(
                    "synthesis", payload, SynthesisOutput, validate
                )
                directions = [
                    finalize_direction(
                        candidate,
                        changes=by_id,
                        project_id=project_id,
                        scan_id=scan_id,
                        previous=previous,
                        window=window,
                        coverage=coverage_status,
                        sources=sources,
                    )
                    for candidate in synthesis.directions
                ]
                synthesis_ok = True
            except (RuntimeError, ValueError):
                notices.append(
                    "本期全局综合暂未完成。已保留全部变化，没有用少数重点冒充完整环境报告。"
                )
        self.progress("assembling_report", "正在保存本期报告与证据关联", len(changes), len(changes))
        featured: list[ProductChange] = []
        for change_id in dict.fromkeys(synthesis.featured_change_ids):
            item = by_id[change_id]
            if (
                item.corpus_role == "external_environment"
                and item.relevant
                and item.interpretation_status == "ready"
            ):
                featured.append(item.model_copy(update={"featured": True}))
        # Featured is presentation only. No deep-dive job is ever created here.
        for item in featured:
            self.repository.save_insight(scan_id, item)
        report = EnvironmentReport(
            data_origin="replay" if data_origin == "replay" else "live",
            report_id=identity("report-", [scan_id, INTELLIGENCE_VERSION]),
            scan_id=scan_id,
            project_id=project_id,
            profile_revision_id=profile_revision_id,
            created_at=utc_now(),
            window=window,
            status="complete" if synthesis_ok and not failed else "degraded",
            coverage_status=coverage_status,
            counts={
                "observations": observed_count,
                "changes": len(changes),
                "external_changes": len(external_changes),
                "project_activity": len(project_activity),
                "interpreted": len(changes) - failed,
                "unavailable": failed,
                "relevant": sum(c.relevant for c in changes),
                "featured": len(featured),
                "directions": len(directions),
                "cache_hits": cache_hits,
                "automatic_deep_dives": 0,
            },
            brief=synthesis.brief,
            directions=directions,
            risks=[],
            opportunities=[],
            featured=featured,
            notices=notices,
            sources=sources,
        )
        self.repository.save_report(
            report,
            {
                "version": INTELLIGENCE_VERSION,
                "calls": self.caller.audit,
                "full_corpus_ids": list(by_id),
                "full_corpus_hash": identity("corpus-", payload),
                "shallow_policy": self.caller.policy.fingerprint("shallow"),
                "synthesis_policy": self.caller.policy.fingerprint("synthesis"),
            },
        )
        self.progress(
            "complete",
            "本期环境报告已更新" if report.status == "complete" else "资料已保存，部分分析仍待完成",
            len(changes) - failed,
            len(changes),
            status="complete" if report.status == "complete" else "degraded",
        )
        return report

    async def _interpret(
        self,
        scan_id: str,
        profile_revision_id: str,
        profile: dict[str, Any],
        digests: list[ChangeDigest],
    ) -> tuple[list[ProductChange], int]:
        products: dict[str, ProductChange] = {}
        pending: list[ChangeDigest] = []
        keys = {
            d.change_id: identity(
                "insight-",
                [
                    d.revision_id,
                    profile_revision_id,
                    CHANGE_INSIGHT_VERSION,
                    self.caller.policy.fingerprint("shallow"),
                ],
            )
            for d in digests
        }
        for digest in digests:
            cached = self.repository.get_cached_insight(keys[digest.change_id])
            if cached is not None:
                try:
                    insight = ShallowInsight.model_validate(cached["insight"])
                    item = project_change(digest, insight, profile)
                    products[digest.change_id] = item
                    self.repository.save_insight(scan_id, item)
                    continue
                except (ValueError, KeyError):
                    pass
            pending.append(digest)
        hits = len(products)
        self.progress("interpreting_changes", "正在逐批理解每一个变化", hits, len(digests))

        async def interpret_batch(batch: list[ChangeDigest]) -> None:
            expected = {d.change_id for d in batch}
            digest_by_id = {d.change_id: d for d in batch}
            validated_products: dict[str, ProductChange] = {}

            def validate(output: InsightBatch) -> None:
                returned = [row.change_id for row in output.results]
                if set(returned) != expected or len(returned) != len(expected):
                    raise ValueError("Every input Change ID must appear exactly once")
                for row in output.results:
                    validated_products[row.change_id] = project_change(
                        digest_by_id[row.change_id], row, profile
                    )

            try:
                output = await self.caller.complete(
                    "shallow",
                    {
                        "project": project_context(profile),
                        "changes": [compact_digest(d) for d in batch],
                    },
                    InsightBatch,
                    validate,
                )
                for insight in output.results:
                    item = validated_products[insight.change_id]
                    products[item.change_id] = item
                    self.repository.cache_insight(
                        keys[item.change_id],
                        {
                            "insight": insight.model_dump(mode="json"),
                            "provenance": self.caller.audit[-1:],
                        },
                    )
                    self.repository.save_insight(scan_id, item)
            except (RuntimeError, ValueError):
                # Large structured batches can fail even when smaller subsets are healthy.
                # Split deterministically; never drop to Top-K and never recurse to one call/item
                # unless the original failing batch is already very small.
                if len(batch) > self.caller.policy.retry_split_min_batch:
                    middle = len(batch) // 2
                    await interpret_batch(batch[:middle])
                    await interpret_batch(batch[middle:])
                    return
                for digest in batch:
                    item = project_change(digest, None, profile)
                    products[digest.change_id] = item
                    self.repository.save_insight(scan_id, item)
            self.progress(
                "interpreting_changes", "正在逐批理解每一个变化", len(products), len(digests)
            )

        for batch in self._batches(pending):
            await interpret_batch(batch)
        return [products[d.change_id] for d in digests], hits

    def _batches(self, digests: list[ChangeDigest]) -> list[list[ChangeDigest]]:
        batches: list[list[ChangeDigest]] = []
        current: list[ChangeDigest] = []
        size = 0
        for digest in digests:
            item_size = len(digest.model_dump_json().encode())
            if current and (
                len(current) >= self.caller.policy.batch_size
                or size + item_size > self.caller.policy.batch_input_bytes
            ):
                batches.append(current)
                current, size = [], 0
            current.append(digest)
            size += item_size
        if current:
            batches.append(current)
        return batches
