"""End-to-end SignalHarness scan workflow."""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, cast
from uuid import uuid4

from signal_harness.utils.fs import atomic_write_text
from signal_harness.agent_integration.mode import RunMode
from signal_harness.agent_integration.runner import LLMAgentTeamRunner
from signal_harness.agent_integration.schemas import LearningPolicyOutput
from signal_harness.alerts import AlertPolicy, write_alert_outputs
from signal_harness.agents import SupervisorAgent
from signal_harness.memory import MemoryBundle
from signal_harness.providers.adapter import AgentProvider
from signal_harness.providers.mock_provider import MockProvider
from signal_harness.runtime.cache import SourceFetchCache
from signal_harness.runtime.permissions import SignalPermissionGuard
from signal_harness.runtime.tool_executor import SignalToolExecutor
from signal_harness.runtime.tool_registry import create_signal_tool_registry
from signal_harness.runtime.tracing import TraceRecorder
from signal_harness.signal.deduplicator import (
    deduplicate_events,
    load_seen_hashes,
    save_seen_signals,
)
from signal_harness.signal.clustering import SignalClusterer
from signal_harness.signal.feedback import load_feedback_history
from signal_harness.signal.noise import NoiseFilter
from signal_harness.signal.normalizer import (
    normalize_event,
    normalize_github_event,
    normalize_rss_item,
)
from signal_harness.signal.schemas import (
    SignalAssessment,
    SignalEvent,
    SourceTask,
    TraceStep,
)
from signal_harness.ui.dashboard import write_dashboard


@dataclass(frozen=True)
class ScanResult:
    signals: list[SignalEvent]
    assessments: list[SignalAssessment]
    output_dir: Path
    trace: TraceRecorder
    failed_sources: list[str]
    source_tasks: list[SourceTask]


@dataclass(frozen=True)
class CollectionBatch:
    events: list[dict[str, Any]]
    failed_sources: list[str]
    source_tasks: list[SourceTask]


@dataclass(frozen=True)
class SourceJob:
    tool_name: str
    arguments: dict[str, Any]
    source_name: str
    source_type: str
    ttl_seconds: int


class SignalHarnessWorkflow:
    """Coordinate config, collection, normalization, agents, memory, and reports."""

    def __init__(
        self,
        *,
        cwd: str | Path,
        config_dir: str | Path | None = None,
        output_dir: str | Path | None = None,
        state_dir: str | Path | None = None,
        mode: RunMode | str = RunMode.DEMO,
        provider: AgentProvider | None = None,
    ) -> None:
        self.cwd = Path(cwd).expanduser().resolve()
        self.config_dir = self._resolve(config_dir or "configs")
        self.output_dir = self._resolve(output_dir or "outputs")
        self.state_dir = self._resolve(state_dir or ".signal-harness")
        self.mode = RunMode(mode)
        self.provider = provider
        self.source_cache = SourceFetchCache(self.state_dir / "cache")
        self.trace = TraceRecorder()
        self.executor = SignalToolExecutor(
            create_signal_tool_registry(),
            cwd=self.cwd,
            metadata={
                "config_dir": str(self.config_dir),
                "output_dir": str(self.output_dir),
                "state_dir": str(self.state_dir),
                "mode": self.mode.value,
                "allow_mock_tool_eval": self.mode is RunMode.MOCK_AGENT,
            },
        )

    def _resolve(self, path: str | Path) -> Path:
        target = Path(path).expanduser()
        if not target.is_absolute():
            target = self.cwd / target
        return target.resolve()

    async def scan(
        self,
        *,
        fixture: str | Path | None = None,
        since: datetime | None = None,
    ) -> ScanResult:
        run_id = f"run-{uuid4().hex[:12]}"
        with self.trace.step("load_config", input_count=3) as state:
            profile, policy, watchlist = await self._load_config()
            state["output_count"] = 3
        guard = SignalPermissionGuard(policy)
        learning_observation: LearningPolicyOutput | None = None

        with self.trace.step("collect_signals") as state:
            if fixture is not None:
                guard.require("read_mock_web_change")
                started = datetime.now(timezone.utc)
                started_clock = time.perf_counter()
                fixture_events = await self._load_fixture(fixture)
                finished = datetime.now(timezone.utc)
                collection = CollectionBatch(
                    events=fixture_events,
                    failed_sources=[],
                    source_tasks=[
                        SourceTask(
                            task_id=f"source-{uuid4().hex[:12]}",
                            source_name=str(fixture),
                            source_type="web_change",
                            status="success",
                            started_at=started,
                            finished_at=finished,
                            duration_ms=max(
                                0,
                                round((time.perf_counter() - started_clock) * 1000),
                            ),
                            output_count=len(fixture_events),
                            cache_hit=False,
                        )
                    ],
                )
            else:
                collection = await self._collect_watchlist(
                    watchlist,
                    since=since,
                    guard=guard,
                )
            raw_events = collection.events
            state["output_count"] = len(raw_events)
            state["failed_sources"] = collection.failed_sources
            state["source_tasks"] = collection.source_tasks
            state["cache_events"] = [
                f"source:{task.source_type}:{'hit' if task.cache_hit else 'miss'}"
                for task in collection.source_tasks
            ]
            if collection.failed_sources:
                state["detail"] = (
                    f"Partial collection failure: {len(collection.failed_sources)} source(s)"
                )

        with self.trace.step("normalize", input_count=len(raw_events)) as state:
            events = [self._normalize_collected(item) for item in raw_events]
            state["output_count"] = len(events)

        with self.trace.step("deduplicate", input_count=len(events)) as state:
            events, duplicate_ids = deduplicate_events(events)
            state["output_count"] = len(events)
            if duplicate_ids:
                state["detail"] = f"Removed duplicates: {', '.join(duplicate_ids)}"

        signal_memory_path = self.state_dir / "signal_memory.json"
        feedback_path = self.state_dir / "feedback_memory.json"
        seen_hashes = load_seen_hashes(signal_memory_path)
        feedback_history = load_feedback_history(feedback_path)
        with self.trace.step("noise_filter", input_count=len(events)) as state:
            noise_assessments = NoiseFilter().evaluate(
                events,
                policy=policy,
                feedback_history=feedback_history,
                seen_hashes=seen_hashes,
            )
            state["output_count"] = len(noise_assessments)
            state["detail"] = "; ".join(
                f"{item.event_id}:{item.noise_reason}"
                for item in noise_assessments
                if item.noise_reason
            )
        with self.trace.step("cluster_signals", input_count=len(events)) as state:
            clusters = SignalClusterer().cluster(events)
            state["output_count"] = len(clusters)
            state["detail"] = ", ".join(
                f"{cluster.cluster_id}={len(cluster.related_event_ids)}"
                for cluster in clusters
            )
        if self.mode is RunMode.DEMO:
            supervisor = SupervisorAgent(self.executor, trace=self.trace)
            with self.trace.step(
                "deterministic_fallback",
                agent=supervisor.name,
                input_count=len(events),
            ) as state:
                assessments = await supervisor.assess_batch(
                    events,
                    project_profile=profile,
                    policy=policy,
                    seen_hashes=seen_hashes,
                    feedback_history=feedback_history,
                )
                state["output_count"] = len(assessments)
                state["detail"] = (
                    "Demo mode uses the deterministic fallback, not the LLM Agent path."
                )
        else:
            provider = self.provider or self._provider_for_mode()
            runner = LLMAgentTeamRunner(
                provider=provider,
                mode=self.mode,
                trace=self.trace,
                tool_executor=self.executor,
            )
            memory_snapshot = MemoryBundle.from_paths(
                config_dir=self.config_dir,
                state_dir=self.state_dir,
            ).snapshot()
            try:
                with self.trace.step(
                    "agent_team_guardrail",
                    agent="LLMAgentTeamRunner",
                    input_count=len(events),
                ) as state:
                    assessments, learning_observation = await runner.run_scan(
                        events,
                        project_profile=profile,
                        policy=policy,
                        memory_snapshot=memory_snapshot,
                        clusters=clusters,
                        noise_assessments=noise_assessments,
                        failed_sources=collection.failed_sources,
                        run_id=run_id,
                        seen_hashes=seen_hashes,
                        feedback_history=feedback_history,
                    )
                    state["output_count"] = len(assessments)
                    state["detail"] = (
                        "Five LLM Agent calls completed; Python validated schemas, "
                        "scoring, permissions, and fallback."
                    )
            finally:
                if self.provider is None:
                    await provider.close()

        self.state_dir.mkdir(parents=True, exist_ok=True)
        if learning_observation is not None:
            self._save_learning_observation(
                learning_observation,
                run_id=run_id,
            )
        save_seen_signals(signal_memory_path, events, assessments)
        await self._write_outputs(
            events,
            assessments,
            guard=guard,
            policy=policy,
            failed_sources=collection.failed_sources,
            source_tasks=collection.source_tasks,
        )
        return ScanResult(
            events,
            assessments,
            self.output_dir,
            self.trace,
            collection.failed_sources,
            collection.source_tasks,
        )

    def _save_learning_observation(
        self,
        learning: LearningPolicyOutput,
        *,
        run_id: str,
    ) -> None:
        payload = {
            "run_id": run_id,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "learning_summary": learning.learning_summary,
            "memory_sections_read": learning.memory_sections_read,
            "policy_update_proposal": learning.policy_update_proposal.model_dump(
                mode="json"
            ),
            "watchlist_update_proposal": learning.watchlist_update_proposal,
            "skill_update_proposal": learning.skill_update_proposal,
            "requires_approval": True,
        }
        serialized = json.dumps(payload, indent=2, ensure_ascii=False) + "\n"
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        atomic_write_text(
            self.state_dir / "latest_learning_observation.json",
            serialized,
        )
        atomic_write_text(
            self.output_dir / "latest_learning_observation.json",
            serialized,
        )

    def _provider_for_mode(self) -> AgentProvider:
        if self.mode is RunMode.MOCK_AGENT:
            return MockProvider()
        if self.mode is RunMode.AGENT:
            from signal_harness.providers.openharness_provider import OpenHarnessProvider

            return OpenHarnessProvider.from_env()
        raise ValueError("demo mode does not create an LLM provider")

    async def _load_config(self) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
        actions = (
            "load_project_profile",
            "load_signal_policy",
            "load_watchlist",
        )
        results = await asyncio.gather(
            *(self.executor.call("signal_memory", {"action": action}) for action in actions)
        )
        for result in results:
            if result.is_error:
                raise RuntimeError(result.output)
        loaded = [json.loads(result.output) for result in results]
        if not all(isinstance(item, dict) for item in loaded):
            raise RuntimeError("SignalHarness config tools returned invalid payloads")
        return cast(
            tuple[dict[str, Any], dict[str, Any], dict[str, Any]],
            tuple(loaded),
        )

    async def _load_fixture(self, fixture: str | Path) -> list[dict[str, Any]]:
        result = await self.executor.call(
            "web_change",
            {"action": "load_fixture", "fixture": str(fixture)},
        )
        if result.is_error:
            raise RuntimeError(result.output)
        payload = json.loads(result.output)
        if not isinstance(payload, list) or not all(
            isinstance(item, dict) for item in payload
        ):
            raise RuntimeError("Fixture tool returned an invalid event list")
        return cast(list[dict[str, Any]], payload)

    async def _collect_watchlist(
        self,
        watchlist: dict[str, Any],
        *,
        since: datetime | None,
        guard: SignalPermissionGuard,
    ) -> CollectionBatch:
        jobs: list[SourceJob] = []
        for entry in watchlist.get("github", {}).get("repositories", []):
            repo = str(entry.get("repo", ""))
            for event_kind in entry.get("events", []):
                if event_kind == "releases":
                    guard.require("read_github_release")
                    jobs.append(
                        SourceJob(
                            tool_name="github_signal",
                            arguments={
                                "action": "fetch_repo_releases",
                                "repo": repo,
                                "since": since,
                            },
                            source_name=repo,
                            source_type="github_release",
                            ttl_seconds=600,
                        )
                    )
                elif event_kind == "issues":
                    guard.require("read_github_issue")
                    jobs.append(
                        SourceJob(
                            tool_name="github_signal",
                            arguments={
                                "action": "fetch_repo_issues",
                                "repo": repo,
                                "since": since,
                            },
                            source_name=repo,
                            source_type="github_issue",
                            ttl_seconds=600,
                        )
                    )
        for feed in watchlist.get("rss", {}).get("feeds", []):
            guard.require("read_rss")
            jobs.append(
                SourceJob(
                    tool_name="rss_signal",
                    arguments={
                        "action": "fetch_feed",
                        "url": str(feed.get("url", "")),
                    },
                    source_name=str(feed.get("name", "unknown-feed")),
                    source_type="rss",
                    ttl_seconds=900,
                )
            )
        for source in watchlist.get("web_changes", {}).get("sources", []):
            if source.get("adapter") != "fixture":
                continue
            guard.require("read_mock_web_change")
            jobs.append(
                SourceJob(
                    tool_name="web_change",
                    arguments={
                        "action": "load_fixture",
                        "fixture": str(source.get("fixture", "")),
                    },
                    source_name=str(
                        source.get("name")
                        or source.get("fixture")
                        or "web-change"
                    ),
                    source_type="web_change",
                    ttl_seconds=0,
                )
            )

        results = await asyncio.gather(
            *(self._run_source_job(job) for job in jobs)
        )
        collected: list[dict[str, Any]] = []
        failures: list[str] = []
        source_tasks: list[SourceTask] = []
        for payload, task, job in results:
            source_tasks.append(task)
            if task.status in {"failed", "partial_failure"}:
                failures.append(
                    f"{job.source_type}:{job.source_name}: {task.error or 'source failed'}"
                )
                continue
            for item in payload:
                if job.source_type == "web_change":
                    collected.append(item)
                else:
                    collected.append(
                        {
                            "_collector_source_name": job.source_name,
                            "_collector_source_type": job.source_type,
                            "_collector_raw": item,
                        }
                    )
        if not collected:
            if failures:
                raise RuntimeError(
                    "All configured signal sources failed: " + "; ".join(failures)
                )
            raise RuntimeError("No events were collected from the configured watchlist")
        return CollectionBatch(
            events=collected,
            failed_sources=failures,
            source_tasks=source_tasks,
        )

    async def _run_source_job(
        self,
        job: SourceJob,
    ) -> tuple[list[dict[str, Any]], SourceTask, SourceJob]:
        task_id = f"source-{uuid4().hex[:12]}"
        started = datetime.now(timezone.utc)
        started_clock = time.perf_counter()
        cache_key = self.source_cache.key(job.tool_name, job.arguments)
        cached = self.source_cache.get(cache_key) if job.ttl_seconds > 0 else None
        if cached is not None and isinstance(cached.payload, list):
            task = SourceTask(
                task_id=task_id,
                source_name=job.source_name,
                source_type=job.source_type,
                status="success",
                started_at=started,
                finished_at=datetime.now(timezone.utc),
                duration_ms=max(0, round((time.perf_counter() - started_clock) * 1000)),
                output_count=len(cached.payload),
                cache_hit=True,
            )
            return cast(list[dict[str, Any]], cached.payload), task, job
        try:
            result = await asyncio.wait_for(
                self.executor.call(job.tool_name, job.arguments),
                timeout=25,
            )
            if result.is_error:
                raise RuntimeError(result.output)
            payload = json.loads(result.output)
            if not isinstance(payload, list) or not all(
                isinstance(item, dict) for item in payload
            ):
                raise RuntimeError("source tool returned an invalid event list")
            if job.ttl_seconds > 0:
                self.source_cache.put(
                    key=cache_key,
                    source_type=job.source_type,
                    source_name=job.source_name,
                    ttl_seconds=job.ttl_seconds,
                    payload=payload,
                )
            task = SourceTask(
                task_id=task_id,
                source_name=job.source_name,
                source_type=job.source_type,
                status="success",
                started_at=started,
                finished_at=datetime.now(timezone.utc),
                duration_ms=max(0, round((time.perf_counter() - started_clock) * 1000)),
                output_count=len(payload),
                cache_hit=False,
            )
            return cast(list[dict[str, Any]], payload), task, job
        except (TimeoutError, RuntimeError, json.JSONDecodeError) as exc:
            task = SourceTask(
                task_id=task_id,
                source_name=job.source_name,
                source_type=job.source_type,
                status="failed",
                started_at=started,
                finished_at=datetime.now(timezone.utc),
                duration_ms=max(0, round((time.perf_counter() - started_clock) * 1000)),
                error=str(exc),
                output_count=0,
                cache_hit=False,
            )
            return [], task, job

    @staticmethod
    def _normalize_collected(item: dict[str, Any]) -> SignalEvent:
        if "_collector_raw" not in item:
            return normalize_event(item)
        raw = item["_collector_raw"]
        source_name = str(item["_collector_source_name"])
        source_type = str(item["_collector_source_type"])
        if source_type.startswith("github_"):
            return normalize_github_event(
                raw,
                repo=source_name,
                event_kind=source_type,
            )
        return normalize_rss_item(raw, feed_name=source_name)

    async def _write_outputs(
        self,
        events: list[SignalEvent],
        assessments: list[SignalAssessment],
        *,
        guard: SignalPermissionGuard,
        policy: dict[str, Any],
        failed_sources: list[str],
        source_tasks: list[SourceTask],
    ) -> None:
        guard.require("write_local_report")
        payload = {
            "signals": [item.model_dump(mode="json") for item in events],
            "assessments": [item.model_dump(mode="json") for item in assessments],
            "action_items": [
                {"event_id": item.event_id, "items": item.action_items}
                for item in assessments
                if item.action_items
            ],
        }
        for action in ("write_radar_digest", "write_run_summary"):
            with self.trace.step(action, agent="ReportWriterTool", input_count=len(events)):
                result = await self.executor.call(
                    "report_writer",
                    {
                        "action": action,
                        **payload,
                        "failed_sources": failed_sources,
                        "source_tasks": [
                            task.model_dump(mode="json") for task in source_tasks
                        ],
                    },
                )
                if result.is_error:
                    raise RuntimeError(result.output)
        guard.require("write_task_trace")
        self.trace.steps.append(
            TraceStep(
                step="write_json_outputs",
                agent="ReportWriterTool",
                status="success",
                input_count=len(events),
                output_count=4,
                duration_ms=0,
            )
        )
        result = await self.executor.call(
            "report_writer",
            {
                "action": "write_json_outputs",
                **payload,
                "trace": [item.model_dump(mode="json") for item in self.trace.steps],
                "failed_sources": failed_sources,
                "source_tasks": [
                    task.model_dump(mode="json") for task in source_tasks
                ],
            },
        )
        if result.is_error:
            self.trace.steps[-1] = self.trace.steps[-1].model_copy(
                update={"status": "error", "detail": result.output}
            )
            raise RuntimeError(result.output)
        write_alert_outputs(
            output_dir=self.output_dir,
            state_dir=self.state_dir,
            events=events,
            assessments=assessments,
            policy=AlertPolicy.from_signal_policy(policy),
        )
        write_dashboard(self.output_dir)
