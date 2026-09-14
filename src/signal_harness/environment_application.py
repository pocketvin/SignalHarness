"""Shared application layer for the current Direction-first environment product.

REST, CLI and MCP adapters should delegate current product semantics here instead of
reconstructing project state, report ownership, profile revisions or feedback attachment
independently. Legacy scan/ProductIntelligence adapters remain separate compatibility surfaces.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any, Callable, Literal

import yaml

from signal_harness.agent_integration.mode import RunMode
from signal_harness.calibration.engine import build_calibration_dataset, evaluate_calibration_replay
from signal_harness.golden_candidates import record_feedback_candidate
from signal_harness.intelligence.contracts import Progress
from signal_harness.intelligence.deep_dive import DeepDiveManager
from signal_harness.learning.staging import load_learning_staging
from signal_harness.memory import FeedbackMemory
from signal_harness.persistence import ChangeLedger
from signal_harness.persistence.intelligence import IntelligenceRepository
from signal_harness.projects.catalog import (
    ProjectOption,
    default_project_id,
    project_catalog,
    project_option,
)
from signal_harness.projects.discovery_profile import with_discovery_profile
from signal_harness.projects.github_onboarding import draft_github_project
from signal_harness.projects.state import prepare_project_state
from signal_harness.providers.task_policy import TaskPolicy
from signal_harness.runtime.permissions import SignalPermissionGuard
from signal_harness.runtime.windows import WindowMode
from signal_harness.service_streaming import StreamRunManager, StreamRunSession
from signal_harness.signal.feedback import (
    create_feedback_record,
    generate_policy_proposal,
    save_policy_proposal,
)
from signal_harness.signal.policy import load_signal_policy, load_yaml_mapping
from signal_harness.signal.schemas import FeedbackLabel
from signal_harness.utils.fs import atomic_write_text

EnvironmentView = Literal["all", "relevant", "featured", "activity", "unavailable"]
ProductFeedbackLabel = Literal["useful", "not_useful", "false_positive", "too_generic"]


class EnvironmentApplication:
    """One application contract for the current Project Environment Intelligence product."""

    def __init__(
        self,
        *,
        cwd: str | Path,
        config_dir: str | Path,
        state_dir: str | Path,
        output_dir: str | Path,
        streams: StreamRunManager | None = None,
        deep_dives: DeepDiveManager | None = None,
    ) -> None:
        self.cwd = Path(cwd).expanduser().resolve()
        self.config_dir = Path(config_dir).expanduser().resolve()
        self.state_dir = Path(state_dir).expanduser().resolve()
        self.output_dir = Path(output_dir).expanduser().resolve()
        self.streams = streams
        self.deep_dives = deep_dives

    # ---- project/profile -------------------------------------------------

    def project(self, project_id: str | None = None) -> ProjectOption:
        return project_option(project_id or default_project_id(self.config_dir), self.config_dir)

    def ledger(self, project_id: str) -> ChangeLedger:
        self.project(project_id)
        state = prepare_project_state(self.state_dir, project_id, migrate_legacy_default=True)
        return ChangeLedger(state / "change_ledger.sqlite3")

    def repository(self, project_id: str) -> IntelligenceRepository:
        return IntelligenceRepository(self.ledger(project_id).path)

    def profile_snapshot(self, project_id: str) -> dict[str, Any]:
        option = self.project(project_id)
        auto_profile = with_discovery_profile(load_yaml_mapping(option.project_profile_path))
        return self.ledger(project_id).ensure_profile_revision(
            project_id=project_id,
            auto_profile=auto_profile,
        )

    def architecture(self, project_id: str) -> dict[str, Any]:
        snapshot = self.profile_snapshot(project_id)
        profile = snapshot.get("effective_profile")
        architecture = profile.get("architecture_snapshot") if isinstance(profile, dict) else None
        return {
            "project_id": project_id,
            "profile_revision_id": snapshot.get("profile_revision_id"),
            "architecture_snapshot": architecture if isinstance(architecture, dict) else None,
        }

    async def refresh_architecture(self, project_id: str) -> dict[str, Any]:
        option = self.project(project_id)
        policy = load_signal_policy(self.config_dir / "signal_policy.yaml")
        guard = SignalPermissionGuard(policy)
        guard.require("modify_project_profile", confirmed=True)
        guard.require("read_github_discovery")
        current = load_yaml_mapping(option.project_profile_path)
        repository = current.get("repository")
        if not isinstance(repository, dict) or str(repository.get("provider") or "") != "github":
            raise ValueError("当前项目没有可重新读取的 GitHub 仓库连接。")
        url = str(repository.get("url") or "").strip()
        if not url:
            repo = str(repository.get("repo") or "").strip()
            url = f"https://github.com/{repo}" if repo else ""
        if not url:
            raise ValueError("GitHub 仓库地址不可用。")
        draft = await draft_github_project(url)
        architecture = draft.project_profile.get("architecture_snapshot")
        if not isinstance(architecture, dict):
            raise ValueError("项目结构分析没有返回可保存结果。")
        updated = dict(current)
        updated["architecture_snapshot"] = architecture
        atomic_write_text(
            option.project_profile_path,
            yaml.safe_dump(updated, allow_unicode=True, sort_keys=False),
        )
        return self.profile_snapshot(project_id)

    # ---- current environment read model ---------------------------------

    def meta(self) -> dict[str, Any]:
        policy = TaskPolicy.load(self.config_dir)
        return {
            "projects": [item.public_payload() for item in project_catalog(self.config_dir)],
            "default_project_id": default_project_id(self.config_dir),
            "intelligence_ready": bool(
                policy.providers("shallow") and policy.providers("synthesis")
            ),
        }

    def active_run(self, project_id: str) -> dict[str, Any] | None:
        if self.streams is None:
            return None
        for session in reversed(list(self.streams.sessions.values())):
            if (
                session.project.id == project_id
                and session.intelligence_pipeline
                and session.status in {"queued", "running"}
            ):
                return self._public_environment_run(session)
        return None

    def home(self, project_id: str) -> dict[str, Any]:
        repo = self.repository(project_id)
        return {
            "report": repo.latest_report(project_id),
            "history": repo.reports(project_id),
            "active_run": self.active_run(project_id),
        }

    def report(self, project_id: str, scan_id: str | None = None) -> dict[str, Any] | None:
        repo = self.repository(project_id)
        value = repo.report(scan_id) if scan_id else repo.latest_report(project_id)
        if value is not None and value.get("project_id") != project_id:
            raise ValueError("该项目下没有这份环境报告")
        return value

    def require_report(self, project_id: str, scan_id: str | None = None) -> dict[str, Any]:
        value = self.report(project_id, scan_id)
        if value is None:
            raise ValueError("该项目下没有环境报告")
        return value

    def reports(self, project_id: str) -> list[dict[str, Any]]:
        return self.repository(project_id).reports(project_id)

    def changes(
        self,
        project_id: str,
        scan_id: str,
        *,
        view: EnvironmentView = "relevant",
        query: str = "",
        offset: int = 0,
        limit: int = 25,
        direction_id: str | None = None,
    ) -> dict[str, Any]:
        saved = self.require_report(project_id, scan_id)
        ids: list[str] | None = None
        effective_view: EnvironmentView = view
        if direction_id:
            direction = next(
                (
                    item
                    for item in saved.get("directions", [])
                    if isinstance(item, dict) and item.get("direction_id") == direction_id
                ),
                None,
            )
            if direction is None:
                raise ValueError("该报告中未找到这个方向")
            ids = list(
                dict.fromkeys(
                    [
                        *list(direction.get("supporting_change_ids") or []),
                        *list(direction.get("contradicting_change_ids") or []),
                    ]
                )
            )
            effective_view = "all"
        return self.repository(project_id).changes(
            scan_id,
            view=effective_view,
            query=query,
            offset=offset,
            limit=limit,
            ids=ids,
        )

    def change(self, project_id: str, scan_id: str, change_id: str) -> dict[str, Any]:
        self.require_report(project_id, scan_id)
        item = self.repository(project_id).change(scan_id, change_id)
        if item is None:
            raise ValueError("这份报告中没有该变化")
        return item

    def activity_summary(self, project_id: str, scan_id: str) -> dict[str, Any]:
        self.require_report(project_id, scan_id)
        return self.repository(project_id).activity_summary(scan_id)

    def direction_history(self, project_id: str, direction_id: str) -> dict[str, Any]:
        repo = self.repository(project_id)
        with repo.connect() as db:
            rows = db.execute(
                "SELECT payload_json,created_at FROM environment_direction_revisions "
                "WHERE project_id=? AND direction_id=? ORDER BY created_at DESC LIMIT 20",
                (project_id, direction_id),
            ).fetchall()
        return {
            "items": [
                {**json.loads(str(row[0])), "created_at": str(row[1])}
                for row in rows
            ]
        }

    def trace(self, project_id: str, scan_id: str) -> list[dict[str, Any]]:
        saved = self.require_report(project_id, scan_id)
        canonical_scan_id = str(saved["scan_id"])
        path = self.output_dir / "service-runs" / canonical_scan_id / "agent_trace.json"
        if not path.is_file():
            return []
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return []
        if not isinstance(payload, list):
            return []
        public_fields = (
            "step",
            "status",
            "agent",
            "agent_name",
            "input_count",
            "output_count",
            "duration_ms",
            "provider",
            "model",
            "fallback_used",
            "cache_hit",
            "retry_count",
            "prompt_tokens",
            "completion_tokens",
            "total_tokens",
        )
        from signal_harness.signal.schemas import TraceStep

        result: list[dict[str, Any]] = []
        public_metadata_fields = ("attempt", "validation_code", "repair_mode", "failure_kind")
        for item in payload:
            try:
                step = TraceStep.model_validate(item)
            except (TypeError, ValueError):
                continue
            public = {field: getattr(step, field) for field in public_fields}
            metadata = {
                key: step.metadata[key]
                for key in public_metadata_fields
                if key in step.metadata
                and isinstance(step.metadata[key], (str, int, float, bool, type(None)))
            }
            if metadata:
                public["metadata"] = metadata
            result.append(public)
        return result

    # ---- current product scan actions -----------------------------------

    def ensure_intelligence_ready(self) -> None:
        policy = TaskPolicy.load(self.config_dir)
        if not policy.providers("shallow") or not policy.providers("synthesis"):
            raise RuntimeError("分析服务尚未配置可用凭据")

    def _start_environment_session(
        self,
        manager: StreamRunManager,
        project_id: str,
        *,
        window: WindowMode = "since_last",
        window_from: Any = None,
        window_to: Any = None,
    ) -> StreamRunSession:
        self.ensure_intelligence_ready()
        project = self.project(project_id)
        session = manager.start(
            source_mode="live",
            fixture=None,
            since=window_from,
            until=window_to,
            window_mode=window,
            mode=RunMode.AGENT,
            provider_id=None,
            project=project,
            max_events=None,
            max_events_per_source=None,
            intelligence_pipeline=True,
        )
        manager.ensure_started(session)
        return session

    def start_scan(
        self,
        project_id: str,
        *,
        window: WindowMode = "since_last",
        window_from: Any = None,
        window_to: Any = None,
    ) -> dict[str, Any]:
        if self.streams is None:
            raise RuntimeError("当前接口没有流式 Scan runtime")
        self.repository(project_id)
        running = self.active_run(project_id)
        if running:
            return running
        session = self._start_environment_session(
            self.streams,
            project_id,
            window=window,
            window_from=window_from,
            window_to=window_to,
        )
        return self._public_environment_run(session)

    async def cancel_scan(self, project_id: str, run_id: str) -> dict[str, Any]:
        if self.streams is None:
            raise RuntimeError("当前接口没有流式 Scan runtime")
        self.repository(project_id)
        session = await self.streams.cancel(run_id, project_id=project_id)
        return self._public_environment_run(session)

    async def run_scan(
        self,
        project_id: str,
        *,
        window: WindowMode = "since_last",
        progress_listener: Callable[[Progress], None] | None = None,
    ) -> dict[str, Any] | None:
        """Blocking CLI adapter over the exact StreamRunManager product execution path."""

        manager = StreamRunManager(
            cwd=self.cwd,
            config_dir=self.config_dir,
            output_dir=self.output_dir,
            state_dir=self.state_dir,
        )
        session = self._start_environment_session(manager, project_id, window=window)
        try:
            if progress_listener is not None:
                async for event in session.subscribe():
                    if event.event == "product.progress":
                        progress_listener(Progress.model_validate(event.data))
            elif session.task is not None:
                await asyncio.gather(session.task, return_exceptions=True)
            if session.task is not None and not session.task.done():
                await asyncio.gather(session.task, return_exceptions=True)
            if session.status != "success":
                message = "environment scan did not complete successfully"
                if isinstance(session.result, dict) and session.result.get("error"):
                    message = str(session.result["error"])
                raise RuntimeError(message)
            return self.repository(project_id).report(session.run_id)
        finally:
            await manager.shutdown()

    # ---- feedback / evolution -------------------------------------------

    def record_feedback(
        self,
        project_id: str,
        scan_id: str,
        change_id: str,
        *,
        label: ProductFeedbackLabel,
        note: str = "",
        source: str = "product",
    ) -> dict[str, Any]:
        self.require_report(project_id, scan_id)
        policy = load_signal_policy(self.config_dir / "signal_policy.yaml")
        guard = SignalPermissionGuard(policy)
        guard.require("save_feedback")
        guard.require("save_policy_proposal")
        ledger = self.ledger(project_id)
        frozen = ledger.scan_change(scan_id=scan_id, change_id=change_id)
        if frozen is None:
            raise ValueError("Change not found in frozen Scan")
        event_id = str(frozen["event_id"])
        record = create_feedback_record(event_id, FeedbackLabel(label), note)
        durable = ledger.record_calibration_feedback(
            project_id=project_id,
            scan_id=scan_id,
            change_id=change_id,
            event_revision_id=int(frozen["event_revision_id"]),
            event_id=event_id,
            label=record.feedback.value,
            note=record.note,
            source=source,
            created_at=record.created_at,
        )
        project_state = prepare_project_state(
            self.state_dir, project_id, migrate_legacy_default=True
        )
        golden_candidate = record_feedback_candidate(
            project_state=project_state,
            project_id=project_id,
            run_id=scan_id,
            event_id=event_id,
            label=record.feedback,
            note=record.note,
            source=source,
            frozen_change=frozen,
        )
        memory = FeedbackMemory(project_state / "feedback_memory.json")
        memory.append(record)
        proposal = generate_policy_proposal(memory.load(), policy)
        save_policy_proposal(project_state / "policy_update_proposal.json", proposal)
        return {
            **durable,
            "proposal_id": proposal.proposal_id,
            "policy_applied": False,
            "golden_candidate_id": (
                golden_candidate.candidate_id if golden_candidate is not None else None
            ),
        }

    def record_outcome(
        self,
        project_id: str,
        scan_id: str,
        change_id: str,
        *,
        impact_observed: bool | None = None,
        action_taken: bool | None = None,
        action_helpful: bool | None = None,
        resolved: bool | None = None,
        note: str = "",
        source: str = "product",
        require_environment_report: bool = True,
    ) -> dict[str, Any]:
        if all(
            value is None
            for value in (impact_observed, action_taken, action_helpful, resolved)
        ):
            raise ValueError("outcome requires at least one observed boolean fact")
        ledger = self.ledger(project_id)
        if require_environment_report:
            self.require_report(project_id, scan_id)
        else:
            metadata = ledger.scan_metadata(scan_id)
            if metadata is None or str(metadata.get("project_id") or "") != project_id:
                raise ValueError("Scan not found for project")
        policy = load_signal_policy(self.config_dir / "signal_policy.yaml")
        SignalPermissionGuard(policy).require("save_outcome")
        frozen = ledger.scan_change(scan_id=scan_id, change_id=change_id)
        if frozen is None:
            raise ValueError("Change not found in frozen Scan")
        return ledger.record_outcome(
            project_id=project_id,
            scan_id=scan_id,
            change_id=change_id,
            event_revision_id=int(frozen["event_revision_id"]),
            impact_observed=impact_observed,
            action_taken=action_taken,
            action_helpful=action_helpful,
            resolved=resolved,
            note=note,
            source=source,
        )

    def calibration_status(
        self, project_id: str, *, include_episodes: bool = False
    ) -> dict[str, Any]:
        project_state = prepare_project_state(self.state_dir, project_id, migrate_legacy_default=True)
        snapshot = self.profile_snapshot(project_id)
        ledger = self.ledger(project_id)
        policy = load_signal_policy(self.config_dir / "signal_policy.yaml")
        SignalPermissionGuard(policy).require("read_calibration")
        dataset = build_calibration_dataset(ledger=ledger, project_id=project_id)
        proposal = self._read_json(
            project_state / "policy_update_proposal.json",
            {},
        )
        replay_payload: dict[str, Any] | None = None
        if isinstance(proposal, dict) and isinstance(proposal.get("new_policy"), dict):
            replay_payload = evaluate_calibration_replay(
                dataset,
                project_profile=dict(snapshot["effective_profile"]),
                old_policy=policy,
                proposed_policy=dict(proposal["new_policy"]),
            ).model_dump(mode="json")
        durable_replay = self._read_json(project_state / "calibration_replay.json", None)
        if not isinstance(durable_replay, dict):
            durable_replay = None
        feedback_labels: dict[str, int] = {}
        for item in ledger.list_calibration_feedback(project_id=project_id):
            label = str(item.get("label") or "unknown")
            feedback_labels[label] = feedback_labels.get(label, 0) + 1
        staged = load_learning_staging(project_state)
        staged_payload = [
            {
                "proposal_id": item.proposal_id,
                "status": item.status,
                "created_at": item.created_at,
                "applied_at": item.applied_at,
                "approved": item.approved,
                "risk": {
                    "risk_level": item.risk.risk_level,
                    "auto_stage_allowed": item.risk.auto_stage_allowed,
                    "apply_requires_approval": item.risk.apply_requires_approval,
                    "replay_gate_passed": item.risk.replay_gate_passed,
                    "reasons": list(item.risk.reasons),
                },
            }
            for item in staged
        ]
        revisions: list[dict[str, Any]] = []
        revision_root = project_state / "policy_revisions"
        if revision_root.is_dir():
            for path in sorted(revision_root.glob("policy-revision-*.json"), reverse=True):
                value = self._read_json(path, None)
                if not isinstance(value, dict):
                    continue
                revisions.append(
                    {
                        "revision_id": value.get("revision_id"),
                        "proposal_id": value.get("proposal_id"),
                        "created_at": value.get("created_at"),
                        "rolled_back_at": value.get("rolled_back_at"),
                    }
                )
        minimum_labeled = 3
        latest_staged = staged[-1] if staged else None
        if latest_staged is not None and latest_staged.status == "applied":
            learning_state = "applied"
        elif latest_staged is not None and latest_staged.status in {"staged", "blocked"}:
            durable_gate_passed = (
                isinstance(durable_replay, dict)
                and durable_replay.get("promotion_allowed") is True
            )
            replay_gate_passed = (
                replay_payload is None or replay_payload.get("promotion_allowed") is True
            )
            learning_state = (
                "review"
                if latest_staged.status == "staged" and durable_gate_passed and replay_gate_passed
                else "candidate_blocked"
            )
        elif replay_payload and replay_payload.get("promotion_allowed") is True:
            learning_state = "candidate_ready"
        elif replay_payload is not None and dataset.labeled_count >= minimum_labeled:
            learning_state = "candidate_blocked"
        elif dataset.labeled_count >= minimum_labeled:
            learning_state = "ready_for_replay"
        else:
            learning_state = "collecting"
        candidate: dict[str, Any] | None = None
        if isinstance(proposal, dict) and proposal:
            old_policy = proposal.get("old_policy")
            new_policy = proposal.get("new_policy")
            candidate = {
                "proposal_id": proposal.get("proposal_id"),
                "reason": proposal.get("reason"),
                "expected_effect": proposal.get("expected_effect"),
                "changed_keywords": list(proposal.get("changed_keywords") or []),
                "changed_sources": list(proposal.get("changed_sources") or []),
                "requires_approval": bool(proposal.get("requires_approval", True)),
                "policy_changes": self._mapping_diff(
                    old_policy if isinstance(old_policy, dict) else {},
                    new_policy if isinstance(new_policy, dict) else {},
                ),
            }
        return {
            "project_id": project_id,
            "dataset_version": dataset.version,
            "feedback_count": dataset.feedback_count,
            "outcome_count": dataset.outcome_count,
            "episode_count": len(dataset.episodes),
            "labeled_count": dataset.labeled_count,
            "positive_count": dataset.positive_count,
            "negative_count": dataset.negative_count,
            "ambiguous_count": dataset.ambiguous_count,
            "unlabeled_count": dataset.unlabeled_count,
            "orphan_feedback_count": dataset.orphan_feedback_count,
            "feedback_labels": feedback_labels,
            "minimum_labeled_required": minimum_labeled,
            "labels_needed": max(0, minimum_labeled - dataset.labeled_count),
            "ready_for_replay": dataset.labeled_count >= minimum_labeled,
            "learning_state": learning_state,
            "candidate": candidate,
            "candidate_replay": replay_payload,
            "durable_replay": durable_replay,
            "staged_proposals": staged_payload,
            "policy_revisions": revisions[:20],
            "revision_count": len(revisions),
            "episodes": (
                [item.model_dump(mode="json") for item in dataset.episodes]
                if include_episodes
                else []
            ),
        }

    # ---- helpers ---------------------------------------------------------

    @staticmethod
    def _public_environment_run(session: StreamRunSession) -> dict[str, Any]:
        return {
            "run_id": session.run_id,
            "status": session.status,
            "progress": session.progress,
            "window": session.frozen_window,
            "events_url": f"/stream-runs/{session.run_id}/events",
        }

    @staticmethod
    def _read_json(path: Path, default: Any) -> Any:
        if not path.is_file():
            return default
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return default

    @classmethod
    def _mapping_diff(
        cls,
        old: dict[str, Any],
        new: dict[str, Any],
        *,
        prefix: str = "",
        limit: int = 16,
    ) -> list[dict[str, Any]]:
        """Return a bounded, presentation-safe leaf diff for a learning candidate."""

        changes: list[dict[str, Any]] = []
        for key in sorted(set(old) | set(new)):
            path = f"{prefix}.{key}" if prefix else str(key)
            left = old.get(key)
            right = new.get(key)
            if isinstance(left, dict) and isinstance(right, dict):
                nested = cls._mapping_diff(
                    left,
                    right,
                    prefix=path,
                    limit=max(0, limit - len(changes)),
                )
                changes.extend(nested)
            elif left != right:
                changes.append({"path": path, "old": left, "new": right})
            if len(changes) >= limit:
                break
        return changes[:limit]
