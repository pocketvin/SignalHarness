"""Explicit-click deep dives with separate durable state and idempotent reservations."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any
from uuid import uuid4

from signal_harness.intelligence.contracts import DEEP_DIVE_VERSION, DeepDiveOutput
from signal_harness.intelligence.corpus import identity
from signal_harness.intelligence.engine import project_context
from signal_harness.intelligence.model_calls import BoundedModelCaller
from signal_harness.intelligence.usage import inspect_usage
from signal_harness.persistence import ChangeLedger
from signal_harness.persistence.intelligence import IntelligenceRepository, utc_now
from signal_harness.projects.catalog import project_catalog, project_option
from signal_harness.projects.state import prepare_project_state
from signal_harness.providers.task_policy import TaskPolicy
from signal_harness.runtime.permissions import SignalPermissionGuard
from signal_harness.runtime.tracing import TraceRecorder
from signal_harness.signal.policy import load_signal_policy, load_yaml_mapping
from signal_harness.tools.web_snapshot import fetch_public_text, normalize_web_text


class DeepDiveManager:
    """Single-process tasks backed by the same project ledger; GET never starts work."""

    def __init__(self, config_dir: Path, state_dir: Path) -> None:
        self.config_dir, self.state_dir = config_dir, state_dir
        self.tasks: dict[str, asyncio.Task[None]] = {}
        self.lock = asyncio.Lock()
        self.caller_factory: Callable[[], BoundedModelCaller] = lambda: BoundedModelCaller(
            TaskPolicy.load(config_dir), TraceRecorder()
        )

    def repository(self, project_id: str) -> IntelligenceRepository:
        project_option(project_id, self.config_dir)
        state = prepare_project_state(self.state_dir, project_id, migrate_legacy_default=True)
        ledger = ChangeLedger(state / "change_ledger.sqlite3")
        return IntelligenceRepository(ledger.path)

    async def startup(self) -> None:
        for project in project_catalog(self.config_dir):
            self.repository(project.id).interrupt_jobs(project.id)

    async def shutdown(self) -> None:
        for task in self.tasks.values():
            task.cancel()
        if self.tasks:
            await asyncio.gather(*self.tasks.values(), return_exceptions=True)
        self.tasks.clear()

    async def start(
        self, project_id: str, scan_id: str, change_id: str, *, retry: bool = False
    ) -> dict[str, Any]:
        repo = self.repository(project_id)
        change = repo.change(scan_id, change_id)
        if change is None:
            raise ValueError("Change not found in this project's scan")
        frozen = repo.profile_for_scan(scan_id)
        guard = SignalPermissionGuard(load_signal_policy(self.config_dir / "signal_policy.yaml"))
        guard.require("read_project_context")
        option = project_option(project_id, self.config_dir)
        watchlist = load_yaml_mapping(option.watchlist_path)
        roots = [
            Path(str(item["path"])).expanduser()
            for item in watchlist.get("local_git", {}).get("repositories", [])
            if isinstance(item, dict) and item.get("path")
        ]
        usage = await asyncio.to_thread(inspect_usage, roots, str(change["entity"]))
        policy = TaskPolicy.load(self.config_dir)
        # Source refresh TTL + usage fingerprint invalidate stale deep-dive answers.
        cache_key = identity(
            "deep-",
            [
                project_id,
                change["revision_id"],
                frozen["profile_revision_id"],
                DEEP_DIVE_VERSION,
                policy.fingerprint("deep_dive"),
                usage["fingerprint"],
                int(time.time() // (6 * 3600)),
            ],
        )
        initial = {
            "job_id": "deep-" + uuid4().hex[:16],
            "cache_key": cache_key,
            "project_id": project_id,
            "scan_id": scan_id,
            "change_id": change_id,
            "revision_id": change["revision_id"],
            "profile_revision_id": frozen["profile_revision_id"],
            "created_at": utc_now(),
            "status": "queued",
            "progress": {"stage": "loading_context", "message": "正在读取这条变化的证据与项目背景"},
            "result": None,
            "usage": usage,
            "sources": [],
            "error": None,
        }
        async with self.lock:
            # Cap concurrent billable user-triggered jobs; clicking many rows cannot exhaust resources.
            self.tasks = {key: task for key, task in self.tasks.items() if not task.done()}
            job, created = repo.create_job(initial)
            should_retry = job["status"] == "error" and retry
            if not created and not should_retry:
                return self.public(job)
            if len(self.tasks) >= 3:
                job["error"] = "已有多个核实任务正在运行，完成后可重试。"
                repo.update_job(job["job_id"], "error", job)
                return self.public(repo.job(job["job_id"], project_id) or job)
            if should_retry:
                job.update(error=None, result=None, usage=usage)
                repo.update_job(job["job_id"], "queued", job)
            self.tasks[job["job_id"]] = asyncio.create_task(
                self._execute(repo, job, change, frozen["profile"], guard)
            )
        return self.public(repo.job(job["job_id"], project_id) or job)

    @staticmethod
    def public(job: dict[str, Any]) -> dict[str, Any]:
        return {
            key: value for key, value in job.items() if key not in {"cache_key", "audit", "trace"}
        }

    async def _execute(
        self,
        repo: IntelligenceRepository,
        job: dict[str, Any],
        change: dict[str, Any],
        profile: dict[str, Any],
        guard: SignalPermissionGuard,
    ) -> None:
        caller = self.caller_factory()

        def stage(name: str, message: str) -> None:
            job["progress"] = {"stage": name, "message": message}
            repo.update_job(job["job_id"], "running", job)

        try:
            stage("resolving_evidence", "正在核对原始来源")
            sources: list[dict[str, Any]] = []
            for evidence in change["evidence"][:3]:
                item = {**evidence, "fetch_status": "snapshot_only", "checked_at": utc_now()}
                if evidence["url"]:
                    try:
                        guard.require("read_web_change")
                        url, kind, text = await asyncio.wait_for(
                            fetch_public_text(evidence["url"], max_bytes=150000), 22
                        )
                        current_text = normalize_web_text(text, content_type=kind)[:9000]
                        item.update(
                            url=url,
                            excerpt=current_text,
                            fetch_status="fetched_current",
                            evidence_id=identity(
                                "fresh-", [evidence["evidence_id"], url, current_text]
                            ),
                            based_on_evidence_id=evidence["evidence_id"],
                        )
                    except (ValueError, RuntimeError, PermissionError, TimeoutError, OSError):
                        item["fetch_status"] = "fetch_unavailable"
                    except Exception:
                        item["fetch_status"] = "fetch_unavailable"
                sources.append(item)
            job["sources"] = sources
            stage("checking_usage", "正在整理已授权项目中的相关引用")
            # inspect_usage ran only after the explicit POST and is part of the cache key.
            stage("analyzing_impact", "正在分析具体影响与可验证的下一步")
            valid_evidence = {e["evidence_id"] for e in [*change["evidence"], *sources]}
            valid_usage = {ref["reference_id"] for ref in job["usage"]["references"]}

            def validate(output: DeepDiveOutput) -> None:
                if not output.evidence_ids or not set(output.evidence_ids) <= valid_evidence:
                    raise ValueError("Deep dive cites missing evidence")
                if not set(output.usage_reference_ids) <= valid_usage:
                    raise ValueError("Deep dive cites nonexistent usage references")

            result = await caller.complete(
                "deep_dive",
                {
                    "change": change,
                    "project": project_context(profile),
                    "sources": sources,
                    "usage_references": job["usage"]["references"],
                    "usage_notice": job["usage"]["notice"],
                },
                DeepDiveOutput,
                validate,
            )
            job.update(
                result=result.model_dump(mode="json"),
                audit=caller.audit,
                trace=[s.model_dump(mode="json") for s in caller.trace.steps],
                progress={"stage": "complete", "message": "核实结果已保存"},
            )
            repo.update_job(job["job_id"], "complete", job)
        except asyncio.CancelledError:
            job["error"] = "核实任务已中断，原报告没有改变，可重新核实。"
            repo.update_job(job["job_id"], "error", job)
            raise
        except Exception:
            job.update(
                error="本次核实未完成，已保留浅层说明与来源；可点击重试。",
                audit=caller.audit,
                trace=[s.model_dump(mode="json") for s in caller.trace.steps],
            )
            repo.update_job(job["job_id"], "error", job)
