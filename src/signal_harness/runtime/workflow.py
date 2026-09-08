"""End-to-end SignalHarness scan workflow."""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal, cast
from uuid import uuid4

from signal_harness.utils.fs import atomic_write_text
from signal_harness.agent_integration.mode import RunMode
from signal_harness.agent_integration.runner import AgentLoopLimits, LLMAgentTeamRunner
from signal_harness.agent_integration.schemas import LearningPolicyOutput
from signal_harness.alerts import AlertPolicy, write_alert_outputs
from signal_harness.agents import SupervisorAgent
from signal_harness.memory import MemoryBundle
from signal_harness.persistence import ChangeLedger
from signal_harness.providers.adapter import AgentProvider
from signal_harness.providers.factory import provider_from_env
from signal_harness.providers.mock_provider import MockProvider
from signal_harness.runtime.cache import SourceFetchCache
from signal_harness.runtime.permissions import SignalPermissionGuard
from signal_harness.runtime.tool_executor import SignalToolExecutor
from signal_harness.runtime.tool_registry import create_signal_tool_registry
from signal_harness.runtime.tracing import TraceListener, TraceRecorder
from signal_harness.runtime.windows import ResolvedScanWindow, WindowMode, resolve_scan_window
from signal_harness.resources import resolve_config_dir
from signal_harness.signal.candidates import select_candidates
from signal_harness.signal.deltas import annotate_release_lineage
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
from signal_harness.tools.web_snapshot import (
    commit_pending_web_snapshots,
    discard_pending_web_snapshots,
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
    scan_id: str
    window: ResolvedScanWindow
    coverage_status: str
    all_change_count: int
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
class EventLimitResult:
    events: list[dict[str, Any]]
    metadata: dict[str, Any]


@dataclass(frozen=True)
class SourceJob:
    tool_name: str
    arguments: dict[str, Any]
    source_name: str
    source_type: str
    ttl_seconds: int
    official: bool | None = None


class SignalHarnessWorkflow:
    """Coordinate config, collection, normalization, agents, memory, and reports."""

    def __init__(
        self,
        *,
        cwd: str | Path,
        config_dir: str | Path | None = None,
        project_profile_path: str | Path | None = None,
        watchlist_path: str | Path | None = None,
        output_dir: str | Path | None = None,
        state_dir: str | Path | None = None,
        mode: RunMode | str = RunMode.DEMO,
        provider: AgentProvider | None = None,
        agent_loop_limits: AgentLoopLimits | None = None,
        trace_listener: TraceListener | None = None,
        project_id: str | None = None,
    ) -> None:
        self.cwd = Path(cwd).expanduser().resolve()
        self.config_dir = resolve_config_dir(self.cwd, config_dir or "configs")
        self.project_profile_path = self._resolve(
            project_profile_path or self.config_dir / "project_profile.yaml"
        )
        self.watchlist_path = self._resolve(watchlist_path or self.config_dir / "watchlist.yaml")
        self.output_dir = self._resolve(output_dir or "outputs")
        self.state_dir = self._resolve(state_dir or ".signal-harness")
        self.project_id = project_id or self.state_dir.name
        self.ledger = ChangeLedger(self.state_dir / "change_ledger.sqlite3")
        self.mode = RunMode(mode)
        self.provider = provider
        self.agent_loop_limits = agent_loop_limits
        self.source_cache = SourceFetchCache(self.state_dir / "cache")
        self.trace = TraceRecorder(trace_listener)
        self.executor = SignalToolExecutor(
            create_signal_tool_registry(),
            cwd=self.cwd,
            metadata={
                "config_dir": str(self.config_dir),
                "project_profile_path": str(self.project_profile_path),
                "watchlist_path": str(self.watchlist_path),
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
        max_events: int | None = None,
        max_events_per_source: int | None = None,
        scan_id: str | None = None,
        window_mode: WindowMode = "legacy",
        until: datetime | None = None,
        consumer_id: str = "local-owner",
        interactive: bool = True,
    ) -> ScanResult:
        if max_events is not None and max_events < 1:
            raise ValueError("max_events must be a positive integer")
        if max_events_per_source is not None and max_events_per_source < 1:
            raise ValueError("max_events_per_source must be a positive integer")
        run_id = f"run-{uuid4().hex[:12]}"
        active_scan_id = scan_id or f"scan-{uuid4().hex[:12]}"
        window = resolve_scan_window(
            ledger=self.ledger,
            project_id=self.project_id,
            mode=window_mode,
            custom_from=since if window_mode == "custom" else None,
            custom_to=until,
            legacy_since=since,
            consumer_id=consumer_id,
        )
        self.executor.context.metadata["scan_id"] = active_scan_id
        with self.trace.step("load_config", input_count=3) as state:
            auto_profile, policy, watchlist = await self._load_config()
            profile_revision = self.ledger.ensure_profile_revision(
                project_id=self.project_id, auto_profile=auto_profile
            )
            profile = profile_revision["effective_profile"]
            state["output_count"] = 3
            state["metadata"] = {
                "profile_revision_id": profile_revision["profile_revision_id"],
                "active_preferences": len(profile_revision["preferences"]),
            }
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
                            coverage_status="complete",
                            pages_fetched=1,
                        )
                    ],
                )
            else:
                collection = await self._collect_watchlist(
                    watchlist,
                    since=window.lower,
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

        with self.trace.step("time_window_filter", input_count=len(events)) as state:
            before_window = len(events)
            filtered_events: list[SignalEvent] = []
            late_count = 0
            for event in events:
                selected = self._select_for_window(event, window)
                if selected is not None:
                    filtered_events.append(selected)
                    if selected.raw_payload.get("window_exception"):
                        late_count += 1
            events = filtered_events
            state["output_count"] = len(events)
            state["metadata"] = {
                "window": window.public_payload(),
                "late_or_revised_count": late_count,
            }
            dropped_window = before_window - len(events)
            if dropped_window:
                state["detail"] = f"Removed {dropped_window} event(s) outside frozen [L,U)."

        with self.trace.step("deduplicate", input_count=len(events)) as state:
            events, duplicate_ids = deduplicate_events(events)
            events = annotate_release_lineage(events)
            state["output_count"] = len(events)
            if duplicate_ids:
                state["detail"] = f"Removed duplicates: {', '.join(duplicate_ids)}"

        all_events = list(events)
        self.ledger.begin_scan(
            scan_id=active_scan_id,
            project_id=self.project_id,
            collected_count=len(raw_events),
            deduped_count=len(all_events),
            window_mode=window.mode,
            window_start=window.lower,
            window_end=window.upper,
            first_use=window.first_use,
            checkpoint_eligible=window.checkpoint_eligible,
            profile_revision_id=str(profile_revision["profile_revision_id"]),
        )
        self.ledger.record_source_tasks(
            scan_id=active_scan_id, source_tasks=collection.source_tasks
        )
        event_change_ids = self.ledger.persist_observations(all_events)

        signal_memory_path = self.state_dir / "signal_memory.json"
        feedback_path = self.state_dir / "feedback_memory.json"
        seen_hashes = load_seen_hashes(signal_memory_path)
        feedback_history = load_feedback_history(feedback_path)

        with self.trace.step("candidate_funnel", input_count=len(events)) as state:
            funnel = select_candidates(
                events,
                project_profile=profile,
                policy=policy,
                max_events=max_events,
                max_events_per_source=max_events_per_source,
                seen_hashes=seen_hashes,
            )
            events = funnel.events
            state["output_count"] = len(events)
            state["metadata"] = {"candidate_funnel": funnel.metadata}
            dropped = int(funnel.metadata.get("dropped_count", 0))
            if dropped:
                state["detail"] = (
                    "Project-aware candidate funnel applied before Agent execution: "
                    f"before={funnel.metadata['before_count']}, "
                    f"after={funnel.metadata['after_count']}, dropped={dropped}"
                )

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
                f"{cluster.cluster_id}={len(cluster.related_event_ids)}" for cluster in clusters
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
                loop_limits=self.agent_loop_limits,
            )
            self.trace.steps.append(
                TraceStep(
                    step="agent_loop_limits",
                    status="success",
                    agent="LLMAgentTeamRunner",
                    duration_ms=0,
                    provider=str(getattr(provider, "provider", provider.name)),
                    model=provider.model,
                    model_profile=str(getattr(provider, "model_profile", "") or ""),
                    detail=(
                        f"max_schema_retries={runner.loop_limits.max_schema_retries}; "
                        f"max_agent_call_seconds="
                        f"{runner.loop_limits.max_agent_call_seconds}; "
                        f"max_run_seconds={runner.loop_limits.max_run_seconds}; "
                        f"max_total_tool_requests_per_run="
                        f"{runner.loop_limits.max_total_tool_requests_per_run}; "
                        f"max_tool_requests_per_event="
                        f"{runner.loop_limits.max_tool_requests_per_event}; "
                        f"max_tool_output_chars="
                        f"{runner.loop_limits.max_tool_output_chars}; "
                        f"max_repair_rounds_per_run="
                        f"{runner.loop_limits.max_repair_rounds_per_run}; "
                        f"max_repair_events_per_run="
                        f"{runner.loop_limits.max_repair_events_per_run}"
                    ),
                )
            )
            memory_snapshot = MemoryBundle.from_paths(
                config_dir=self.config_dir,
                state_dir=self.state_dir,
                project_profile_path=self.project_profile_path,
                watchlist_path=self.watchlist_path,
            ).snapshot()
            project_memory = memory_snapshot.get("project_memory")
            if isinstance(project_memory, dict):
                project_memory["project_profile"] = profile
                project_memory["profile_revision_id"] = profile_revision["profile_revision_id"]
            try:
                with self.trace.step(
                    "agent_team_guardrail",
                    agent="LLMAgentTeamRunner",
                    input_count=len(events),
                ) as state:
                    assessments, learning_observation = await asyncio.wait_for(
                        runner.run_scan(
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
                            defer_learning=self.mode is RunMode.AGENT,
                        ),
                        timeout=runner.loop_limits.max_run_seconds,
                    )
                    state["output_count"] = len(assessments)
                    state["detail"] = (
                        "Agent decision path completed; Python validated schemas, scoring, "
                        "permissions, and fallback. Interactive real-provider runs defer "
                        "LearningPolicyAgent reflection from the critical path."
                    )
            except (TimeoutError, asyncio.TimeoutError):
                detail = (
                    "agent_team_run_timeout after "
                    f"{runner.loop_limits.max_run_seconds}s; deterministic fallback "
                    "generated complete audit assessments."
                )
                self.trace.steps.append(
                    TraceStep(
                        step="agent_team_run_timeout",
                        status="success",
                        agent="LLMAgentTeamRunner",
                        input_count=len(events),
                        output_count=len(events),
                        duration_ms=0,
                        fallback_used=True,
                        detail=detail,
                        error=detail,
                    )
                )
                supervisor = SupervisorAgent(self.executor, trace=self.trace)
                with self.trace.step(
                    "deterministic_fallback",
                    agent=supervisor.name,
                    input_count=len(events),
                ) as fallback_state:
                    assessments = await supervisor.assess_batch(
                        events,
                        project_profile=profile,
                        policy=policy,
                        seen_hashes=seen_hashes,
                        feedback_history=feedback_history,
                    )
                    fallback_state["output_count"] = len(assessments)
                    fallback_state["detail"] = (
                        "Agent team run timeout used deterministic guardrail fallback."
                    )
            finally:
                if self.provider is None:
                    await provider.close()

        self.state_dir.mkdir(parents=True, exist_ok=True)
        analyzed_event_ids = {event.event_id for event in events}
        self.ledger.freeze_scan_changes(
            scan_id=active_scan_id,
            project_id=self.project_id,
            events=all_events,
            event_change_ids=event_change_ids,
            project_profile=profile,
            policy=policy,
            analyzed_event_ids=analyzed_event_ids,
            assessments=assessments,
        )
        try:
            if learning_observation is not None:
                self._save_learning_observation(
                    learning_observation,
                    run_id=run_id,
                )
            await self._write_outputs(
                events,
                assessments,
                guard=guard,
                policy=policy,
                failed_sources=collection.failed_sources,
                source_tasks=collection.source_tasks,
            )
            commit_pending_web_snapshots(self.state_dir, active_scan_id)
            save_seen_signals(signal_memory_path, events, assessments)
            coverage_status = self._coverage_status(
                collection.source_tasks, collection.failed_sources
            )
            checkpoint_safe = self._checkpoint_safe(
                collection.source_tasks, collection.failed_sources
            )
            self.ledger.complete_scan(
                scan_id=active_scan_id,
                analyzed_count=len(events),
                relevant_count=len(all_events),
                coverage_status=coverage_status,
            )
            if (
                interactive
                and fixture is None
                and window.checkpoint_eligible
                and checkpoint_safe
            ):
                self.ledger.advance_interactive_checkpoint(
                    project_id=self.project_id,
                    checkpoint_at=window.upper,
                    scan_id=active_scan_id,
                    consumer_id=consumer_id,
                )
        except Exception as exc:
            discard_pending_web_snapshots(self.state_dir, active_scan_id)
            self.ledger.fail_scan(scan_id=active_scan_id, error=f"{exc.__class__.__name__}: {exc}")
            raise
        return ScanResult(
            active_scan_id,
            window,
            coverage_status,
            len(all_events),
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
            "policy_update_proposal": learning.policy_update_proposal.model_dump(mode="json"),
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
            return provider_from_env(self.mode, config_dir=self.config_dir)
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
        if not isinstance(payload, list) or not all(isinstance(item, dict) for item in payload):
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
                    official=(bool(feed.get("official")) if "official" in feed else None),
                )
            )
        for source in watchlist.get("web_changes", {}).get("sources", []):
            adapter = str(source.get("adapter") or "").strip().lower()
            source_name = str(
                source.get("name") or source.get("url") or source.get("fixture") or "web-change"
            )
            if adapter == "fixture":
                guard.require("read_mock_web_change")
                jobs.append(
                    SourceJob(
                        tool_name="web_change",
                        arguments={
                            "action": "load_fixture",
                            "fixture": str(source.get("fixture", "")),
                        },
                        source_name=source_name,
                        source_type="web_change",
                        ttl_seconds=0,
                    )
                )
            elif adapter in {"http", "snapshot"}:
                guard.require("read_web_change")
                jobs.append(
                    SourceJob(
                        tool_name="web_change",
                        arguments={
                            "action": "fetch_snapshot",
                            "url": str(source.get("url", "")),
                            "source_name": source_name,
                            "official": bool(source.get("official", False)),
                            "max_bytes": int(source.get("max_bytes") or 750000),
                        },
                        source_name=source_name,
                        source_type="web_change",
                        ttl_seconds=0,
                        official=(bool(source.get("official")) if "official" in source else None),
                    )
                )

        results = await asyncio.gather(*(self._run_source_job(job) for job in jobs))
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
                    raw_item = dict(item)
                    if job.source_type == "rss":
                        raw_item.setdefault("feed_url", str(job.arguments.get("url", "")))
                        raw_item.setdefault(
                            "source_feed_url",
                            str(job.arguments.get("url", "")),
                        )
                        if job.official is not None:
                            raw_item.setdefault("official", job.official)
                    collected.append(
                        {
                            "_collector_source_name": job.source_name,
                            "_collector_source_type": job.source_type,
                            "_collector_raw": raw_item,
                        }
                    )
        if not collected:
            if any(task.status == "success" for task in source_tasks):
                return CollectionBatch(
                    events=[],
                    failed_sources=failures,
                    source_tasks=source_tasks,
                )
            if failures:
                raise RuntimeError("All configured signal sources failed: " + "; ".join(failures))
            raise RuntimeError("No events were collected from the configured watchlist")
        return CollectionBatch(
            events=collected,
            failed_sources=failures,
            source_tasks=source_tasks,
        )

    def _apply_event_limits(
        self,
        events: list[dict[str, Any]],
        *,
        max_events: int | None,
        max_events_per_source: int | None,
    ) -> EventLimitResult:
        if max_events is None and max_events_per_source is None:
            return EventLimitResult(events=events, metadata={})

        indexed = list(enumerate(events))
        before_by_source = self._source_counts(indexed)
        selected = indexed
        if max_events_per_source is not None:
            grouped: dict[str, list[tuple[int, dict[str, Any]]]] = {}
            for pair in selected:
                grouped.setdefault(self._source_key(pair[1]), []).append(pair)
            selected = [
                pair
                for source_key in sorted(grouped)
                for pair in sorted(grouped[source_key], key=self._event_rank)[
                    :max_events_per_source
                ]
            ]
        if max_events is not None:
            selected = self._balanced_event_limit(selected, max_events)
        else:
            selected = sorted(selected, key=self._event_rank)

        after_by_source = self._source_counts(selected)
        metadata: dict[str, Any] = {
            "before_count": len(events),
            "after_count": len(selected),
            "dropped_count": max(0, len(events) - len(selected)),
            "max_events": max_events,
            "max_events_per_source": max_events_per_source,
            "source_counts_before": before_by_source,
            "source_counts_after": after_by_source,
        }
        return EventLimitResult(
            events=[item for _, item in selected],
            metadata=metadata,
        )

    @classmethod
    def _source_counts(
        cls,
        indexed_events: list[tuple[int, dict[str, Any]]],
    ) -> dict[str, int]:
        counts: dict[str, int] = {}
        for _, item in indexed_events:
            key = cls._source_key(item)
            counts[key] = counts.get(key, 0) + 1
        return dict(sorted(counts.items()))

    @classmethod
    def _balanced_event_limit(
        cls,
        indexed_events: list[tuple[int, dict[str, Any]]],
        max_events: int,
    ) -> list[tuple[int, dict[str, Any]]]:
        grouped: dict[str, list[tuple[int, dict[str, Any]]]] = {}
        for pair in indexed_events:
            grouped.setdefault(cls._source_key(pair[1]), []).append(pair)
        for source_key, values in grouped.items():
            grouped[source_key] = sorted(values, key=cls._event_rank)
        source_keys_by_type: dict[str, list[str]] = {}
        for source_key in grouped:
            source_type = source_key.split(":", 1)[0]
            source_keys_by_type.setdefault(source_type, []).append(source_key)
        for source_type, source_keys in source_keys_by_type.items():
            source_keys_by_type[source_type] = sorted(source_keys)
        source_type_order = sorted(
            source_keys_by_type,
            key=lambda source_type: (
                cls._source_type_round_robin_rank(source_type),
                source_type,
            ),
        )
        source_offsets = {source_type: 0 for source_type in source_type_order}
        selected: list[tuple[int, dict[str, Any]]] = []
        while len(selected) < max_events and any(grouped.values()):
            for source_type in source_type_order:
                source_keys = source_keys_by_type[source_type]
                if not any(grouped[source_key] for source_key in source_keys):
                    continue
                start = source_offsets[source_type]
                for offset in range(len(source_keys)):
                    source_index = (start + offset) % len(source_keys)
                    source_key = source_keys[source_index]
                    values = grouped[source_key]
                    if values:
                        selected.append(values.pop(0))
                        source_offsets[source_type] = (source_index + 1) % len(source_keys)
                        break
                if len(selected) >= max_events:
                    break
        return sorted(selected, key=cls._event_rank)

    @staticmethod
    def _source_key(item: dict[str, Any]) -> str:
        if "_collector_raw" in item:
            source_type = str(item.get("_collector_source_type") or "unknown")
            source_name = str(item.get("_collector_source_name") or "unknown")
            return f"{source_type}:{source_name}"
        source_type = str(item.get("source_type") or "unknown")
        source_name = str(item.get("source_name") or "unknown")
        return f"{source_type}:{source_name}"

    @classmethod
    def _event_rank(cls, indexed_event: tuple[int, dict[str, Any]]) -> tuple[float, int]:
        index, item = indexed_event
        return (-cls._event_timestamp(item), index)

    @staticmethod
    def _source_type_round_robin_rank(source_type: str) -> int:
        return {
            "github_release": 0,
            "rss": 1,
            "github_issue": 2,
            "web_change": 3,
        }.get(source_type, 9)

    @classmethod
    def _event_timestamp(cls, item: dict[str, Any]) -> float:
        raw = item.get("_collector_raw") if isinstance(item.get("_collector_raw"), dict) else item
        if not isinstance(raw, dict):
            return 0.0
        for key in (
            "published_at",
            "updated_at",
            "created_at",
            "collected_at",
            "pubDate",
            "date",
        ):
            value = raw.get(key)
            parsed = cls._parse_timestamp(value)
            if parsed is not None:
                return parsed
        return 0.0

    @staticmethod
    def _parse_timestamp(value: object) -> float | None:
        if isinstance(value, datetime):
            normalized = value
        elif isinstance(value, str) and value.strip():
            raw = value.strip()
            if raw.endswith(("Z", "z")):
                raw = raw[:-1] + "+00:00"
            try:
                normalized = datetime.fromisoformat(raw)
            except ValueError:
                return None
        else:
            return None
        if normalized.tzinfo is None:
            normalized = normalized.replace(tzinfo=timezone.utc)
        return normalized.timestamp()

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
                coverage_status=cast(
                    Literal["unknown", "complete", "partial"],
                    str(cached.metadata.get("coverage_status") or "unknown"),
                ),
                pages_fetched=int(cached.metadata.get("pages_fetched") or 0),
                history_limited=bool(cached.metadata.get("history_limited", False)),
                diagnostics=[str(item) for item in cached.metadata.get("diagnostics", [])],
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
            if not isinstance(payload, list) or not all(isinstance(item, dict) for item in payload):
                raise RuntimeError("source tool returned an invalid event list")
            if job.ttl_seconds > 0:
                self.source_cache.put(
                    key=cache_key,
                    source_type=job.source_type,
                    source_name=job.source_name,
                    ttl_seconds=job.ttl_seconds,
                    payload=payload,
                    metadata=result.metadata,
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
                coverage_status=cast(
                    Literal["unknown", "complete", "partial"],
                    str(result.metadata.get("coverage_status") or "unknown"),
                ),
                pages_fetched=int(result.metadata.get("pages_fetched") or 0),
                history_limited=bool(result.metadata.get("history_limited", False)),
                diagnostics=[str(item) for item in result.metadata.get("diagnostics", [])],
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
                coverage_status="partial",
                diagnostics=[str(exc)],
            )
            return [], task, job

    def _select_for_window(
        self, event: SignalEvent, window: ResolvedScanWindow
    ) -> SignalEvent | None:
        if self._is_within_window(event, window.lower, window.upper):
            return event
        # Snapshot diffs are observations created by this Scan, not source facts with a
        # trustworthy occurrence timestamp. Attribute the observation to this Scan while
        # keeping the frozen source-time upper bound strict for timestamped sources.
        if event.source_type == "web_change" and event.raw_payload.get("current_hash"):
            raw_payload = dict(event.raw_payload)
            raw_payload["window_exception"] = "observed_during_scan"
            return event.model_copy(update={"raw_payload": raw_payload})
        if window.mode != "since_last" or window.first_use or window.lower is None:
            return None
        published = event.published_at
        if published is None:
            return None
        if published.tzinfo is None:
            published = published.replace(tzinfo=timezone.utc)
        else:
            published = published.astimezone(timezone.utc)
        lower = window.lower.astimezone(timezone.utc)
        upper = window.upper.astimezone(timezone.utc)
        # Late discovery is only for facts older than L. A future-dated fact at/after U
        # remains outside this frozen Scan even if it is first observed during collection.
        if not (published < lower and published < upper):
            return None
        observation_state = self.ledger.observation_state(event)
        if observation_state == "seen":
            return None
        raw_payload = dict(event.raw_payload)
        raw_payload["window_exception"] = (
            "late_revision" if observation_state == "revision" else "late_discovery"
        )
        return event.model_copy(update={"raw_payload": raw_payload})

    @staticmethod
    def _coverage_status(source_tasks: list[SourceTask], failed_sources: list[str]) -> str:
        if failed_sources or any(
            task.coverage_status == "partial" or task.history_limited for task in source_tasks
        ):
            return "partial"
        if any(task.coverage_status == "unknown" for task in source_tasks):
            return "unknown"
        return "complete"

    @staticmethod
    def _checkpoint_safe(source_tasks: list[SourceTask], failed_sources: list[str]) -> bool:
        return not failed_sources and not any(
            task.coverage_status == "partial" or task.history_limited for task in source_tasks
        )

    @staticmethod
    def _is_within_window(
        event: SignalEvent, lower: datetime | None, upper: datetime
    ) -> bool:
        observed = event.published_at
        if observed is None:
            return True
        if observed.tzinfo is None:
            observed = observed.replace(tzinfo=timezone.utc)
        else:
            observed = observed.astimezone(timezone.utc)
        upper_bound = upper if upper.tzinfo else upper.replace(tzinfo=timezone.utc)
        upper_bound = upper_bound.astimezone(timezone.utc)
        if observed >= upper_bound:
            return False
        if lower is None:
            return True
        lower_bound = lower if lower.tzinfo else lower.replace(tzinfo=timezone.utc)
        return observed >= lower_bound.astimezone(timezone.utc)

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
                        "source_tasks": [task.model_dump(mode="json") for task in source_tasks],
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
                "source_tasks": [task.model_dump(mode="json") for task in source_tasks],
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
