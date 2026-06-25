"""Typed stores for project, signal, feedback, and policy memory."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from signal_harness.signal.feedback import load_feedback_history, save_feedback
from signal_harness.signal.policy import load_signal_policy, load_yaml_mapping
from signal_harness.signal.schemas import FeedbackRecord


@dataclass(frozen=True)
class ProjectMemory:
    """Project profile and watchlist configuration."""

    config_dir: Path

    def load(self) -> dict[str, Any]:
        return {
            "project_profile": load_yaml_mapping(
                self.config_dir / "project_profile.yaml"
            ),
            "watchlist": load_yaml_mapping(self.config_dir / "watchlist.yaml"),
        }


@dataclass(frozen=True)
class SignalMemory:
    """Seen hashes, historical assessments, and previous decisions."""

    path: Path

    def load(self) -> dict[str, Any]:
        if not self.path.exists():
            return {
                "seen_signals": [],
                "duplicate_hashes": [],
                "previous_assessments": [],
            }
        payload = json.loads(self.path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("signal memory must contain a JSON object")
        return payload


@dataclass(frozen=True)
class FeedbackMemory:
    """Immutable useful/not-useful/false-positive/missed feedback records."""

    path: Path

    def load(self) -> list[FeedbackRecord]:
        return load_feedback_history(self.path)

    def append(self, record: FeedbackRecord) -> Path:
        return save_feedback(self.path, record)


@dataclass(frozen=True)
class PolicyMemory:
    """Active policy plus versioned, review-only proposal history."""

    config_dir: Path
    state_dir: Path

    def load(self) -> dict[str, Any]:
        proposal_path = self.state_dir / "policy_update_proposal.json"
        proposal: dict[str, Any] | None = None
        if proposal_path.exists():
            loaded = json.loads(proposal_path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                proposal = loaded
        active = load_signal_policy(self.config_dir / "signal_policy.yaml")
        return {
            "active_policy": active,
            "policy_versions": [active.get("version", 1)],
            "latest_proposal": proposal,
        }


@dataclass(frozen=True)
class MemoryBundle:
    """Aggregate memory snapshot supplied to LearningPolicyAgent."""

    project: ProjectMemory
    signal: SignalMemory
    feedback: FeedbackMemory
    policy: PolicyMemory

    @classmethod
    def from_paths(
        cls,
        *,
        config_dir: str | Path,
        state_dir: str | Path,
    ) -> "MemoryBundle":
        config = Path(config_dir).expanduser().resolve()
        state = Path(state_dir).expanduser().resolve()
        return cls(
            project=ProjectMemory(config),
            signal=SignalMemory(state / "signal_memory.json"),
            feedback=FeedbackMemory(state / "feedback_memory.json"),
            policy=PolicyMemory(config, state),
        )

    def snapshot(self) -> dict[str, Any]:
        return {
            "project_memory": self.project.load(),
            "signal_memory": self.signal.load(),
            "feedback_memory": [
                record.model_dump(mode="json") for record in self.feedback.load()
            ],
            "policy_memory": self.policy.load(),
        }
