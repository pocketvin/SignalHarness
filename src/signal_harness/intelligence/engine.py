"""Full-corpus shallow interpretation followed by ONE global synthesis call."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from typing import Any

from signal_harness.intelligence.contracts import (
    CHANGE_INSIGHT_VERSION,
    INTELLIGENCE_VERSION,
    LEGACY_CHANGE_INSIGHT_VERSIONS,
    ChangeDigest,
    EnvironmentReport,
    ProductChange,
    ShallowModelBatch,
    Progress,
    ShallowInsight,
    SynthesisOutput,
)
from signal_harness.intelligence.batch_planner import BalancedBatchPlanner
from signal_harness.intelligence.corpus import finalize_direction, identity, project_change
from signal_harness.intelligence.direction_digest import organize_direction_corpus
from signal_harness.intelligence.fact_capsule import (
    build_fact_capsule,
    capsule_semantic_payload,
    deterministic_insight,
)
from signal_harness.intelligence.model_calls import BoundedModelCaller, validate_product_language
from signal_harness.intelligence.project_projection import (
    model_row_to_insight,
    project_reference_map,
    reference_id_for_basis,
    shallow_project_context,
)
from signal_harness.intelligence.semantic_router import (
    SemanticWorkItem,
    route_capsule,
    tiny_fast_path_candidate,
)
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
        changes, insight_stats = await self._interpret(
            scan_id, profile_revision_id, profile, digests
        )
        previous = (
            self.repository.latest_report(project_id, before=str(window["from"]))
            if window.get("from")
            else None
        )
        external_changes = [item for item in changes if item.corpus_role == "external_environment"]
        project_activity = [item for item in changes if item.corpus_role == "project_activity"]
        external_corpus = organize_direction_corpus(
            external_changes, fact_chars=self.caller.policy.direction_fact_chars
        )
        activity_corpus = organize_direction_corpus(
            project_activity, fact_chars=self.caller.policy.direction_fact_chars
        )
        payload = {
            "window": window,
            "project": project_context(profile),
            "coverage_status": coverage_status,
            "sources": sources,
            "corpus_legend": external_corpus["legend"],
            "corpus_index": external_corpus["index"],
            "corpus": external_corpus["items"],
            "project_activity_index": activity_corpus["index"],
            "project_activity": activity_corpus["items"],
            "previous_report": {
                key: previous[key] for key in ("window", "directions", "coverage_status")
            }
            if previous
            else None,
            "manifest": {
                "external_count": len(external_changes),
                "project_activity_count": len(project_activity),
            },
        }
        by_id = {item.change_id: item for item in changes}
        direction_digest_bytes = len(
            json.dumps(external_corpus["items"], ensure_ascii=False, separators=(",", ":")).encode()
        )
        evidence_excerpt_bytes = sum(
            len(evidence.excerpt.encode()) for item in changes for evidence in item.evidence
        )
        full_product_change_bytes = sum(len(item.model_dump_json().encode()) for item in changes)
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
                "cache_hits": insight_stats["cache_hits"],
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
                "model_usage": self.caller.usage_summary(),
                "full_corpus_ids": list(by_id),
                "full_corpus_hash": identity("corpus-", payload),
                "shallow_policy": self.caller.policy.fingerprint("shallow"),
                "synthesis_policy": self.caller.policy.fingerprint("synthesis"),
                "insight_routing": insight_stats,
                "direction_digest_bytes": direction_digest_bytes,
                "source_evidence_excerpt_bytes": evidence_excerpt_bytes,
                "full_product_change_bytes": full_product_change_bytes,
                "synthesis_input_bytes": len(
                    json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode()
                ),
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
    ) -> tuple[list[ProductChange], dict[str, Any]]:
        products: dict[str, ProductChange] = {}
        semantic_items: list[SemanticWorkItem] = []
        all_work_items: list[SemanticWorkItem] = []
        policy_fingerprint = self.caller.policy.fingerprint("shallow")

        def cache_key(digest: ChangeDigest, version: str) -> str:
            return identity(
                "insight-",
                [digest.revision_id, profile_revision_id, version, policy_fingerprint],
            )

        current_keys = {
            digest.change_id: cache_key(digest, CHANGE_INSIGHT_VERSION) for digest in digests
        }
        project_references = project_reference_map(profile)
        semantic_project_context = shallow_project_context(profile)
        stats: dict[str, Any] = {
            "cache_hits": 0,
            "legacy_cache_hits": 0,
            "deterministic": 0,
            "semantic": 0,
            "unavailable": 0,
            "semantic_batch_attempts": 0,
            "semantic_batch_splits": 0,
            "planned_semantic_batches": 0,
            "shallow_concurrency": self.caller.policy.shallow_concurrency,
            "tiny_fast_path_candidate": False,
            "tiny_fast_path_activated": False,
        }

        for digest in digests:
            capsule = build_fact_capsule(digest, profile)
            all_work_items.append(SemanticWorkItem(digest=digest, capsule=capsule))
            candidates = [(CHANGE_INSIGHT_VERSION, current_keys[digest.change_id])]
            candidates.extend(
                (version, cache_key(digest, version)) for version in LEGACY_CHANGE_INSIGHT_VERSIONS
            )
            cache_used = False
            for version, key in candidates:
                cached = self.repository.get_cached_insight(key)
                if cached is None:
                    continue
                try:
                    insight = ShallowInsight.model_validate(cached["insight"])
                    validate_product_language(insight)
                    item = project_change(digest, insight, profile)
                except (ValueError, KeyError):
                    continue
                products[digest.change_id] = item
                self.repository.save_insight(scan_id, item)
                stats["cache_hits"] += 1
                if version != CHANGE_INSIGHT_VERSION:
                    stats["legacy_cache_hits"] += 1
                    self.repository.cache_insight(current_keys[digest.change_id], cached)
                cache_used = True
                break
            if cache_used:
                continue

            decision = route_capsule(capsule)
            if decision.route == "deterministic":
                insight = deterministic_insight(capsule)
                item = project_change(digest, insight, profile)
                products[digest.change_id] = item
                self.repository.save_insight(scan_id, item)
                stats["deterministic"] += 1
            else:
                semantic_items.append(SemanticWorkItem(digest=digest, capsule=capsule))

        stats["tiny_fast_path_candidate"] = tiny_fast_path_candidate(
            all_work_items,
            max_changes=self.caller.policy.tiny_fast_path_max_changes,
            max_input_bytes=self.caller.policy.tiny_fast_path_max_input_bytes,
        )
        planner = BalancedBatchPlanner(
            max_items=self.caller.policy.batch_size,
            max_input_bytes=self.caller.policy.batch_input_bytes,
        )
        batches = planner.plan(semantic_items)
        stats["planned_semantic_batches"] = len(batches)
        semaphore = asyncio.Semaphore(self.caller.policy.shallow_concurrency)
        self.progress(
            "interpreting_changes", "正在理解需要语义判断的变化", len(products), len(digests)
        )

        async def interpret_batch(batch: list[SemanticWorkItem]) -> None:
            expected = {item.digest.change_id for item in batch}
            digest_by_id = {item.digest.change_id: item.digest for item in batch}
            validated_products: dict[str, ProductChange] = {}
            converted: dict[str, ShallowInsight] = {}

            def validate(output: ShallowModelBatch) -> None:
                returned = [row.id for row in output.x]
                if set(returned) != expected or len(returned) != len(expected):
                    raise ValueError("Every input Change ID must appear exactly once")
                work_by_id = {item.digest.change_id: item for item in batch}
                for row in output.x:
                    work = work_by_id[row.id]
                    insight = model_row_to_insight(
                        row,
                        references=project_references,
                        digest=work.digest,
                        known_relation=work.capsule.deterministic_relation,
                        known_basis_kind=work.capsule.deterministic_basis_kind,
                        known_basis_label=work.capsule.deterministic_basis_label,
                    )
                    validate_product_language(insight)
                    converted[row.id] = insight
                    validated_products[row.id] = project_change(
                        digest_by_id[row.id], insight, profile
                    )

            try:
                stats["semantic_batch_attempts"] += 1
                async with semaphore:
                    change_payloads = []
                    for work in batch:
                        payload_item = capsule_semantic_payload(work.capsule, work.digest)
                        basis_id = reference_id_for_basis(
                            project_references,
                            kind=work.capsule.deterministic_basis_kind,
                            label=work.capsule.deterministic_basis_label,
                        )
                        if basis_id:
                            payload_item["known_project_basis_id"] = basis_id
                        change_payloads.append(payload_item)
                    output = await self.caller.complete(
                        "shallow",
                        {
                            "project": semantic_project_context,
                            "changes": change_payloads,
                        },
                        ShallowModelBatch,
                        validate,
                    )
                receipt = self.caller.last_receipt()
                for row in output.x:
                    insight = converted[row.id]
                    item = validated_products[row.id]
                    products[item.change_id] = item
                    self.repository.cache_insight(
                        current_keys[item.change_id],
                        {
                            "insight": insight.model_dump(mode="json"),
                            "provenance": [receipt] if receipt else [],
                        },
                    )
                    self.repository.save_insight(scan_id, item)
                    stats["semantic"] += 1
            except (RuntimeError, ValueError):
                # Keep model concurrency bounded even during repair. Split failed work; never
                # truncate to Top-K and never silently replace a semantic result with a fake one.
                if len(batch) > self.caller.policy.retry_split_min_batch:
                    stats["semantic_batch_splits"] += 1
                    middle = len(batch) // 2
                    await asyncio.gather(
                        interpret_batch(batch[:middle]), interpret_batch(batch[middle:])
                    )
                    return
                for work in batch:
                    item = project_change(work.digest, None, profile)
                    products[work.digest.change_id] = item
                    self.repository.save_insight(scan_id, item)
                    stats["unavailable"] += 1
            self.progress(
                "interpreting_changes", "正在理解需要语义判断的变化", len(products), len(digests)
            )

        if batches:
            await asyncio.gather(*(interpret_batch(batch) for batch in batches))
        return [products[digest.change_id] for digest in digests], stats
