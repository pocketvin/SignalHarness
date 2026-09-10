"""Versioned Harness variants and reproducible analysis-input metadata."""

from __future__ import annotations

import hashlib
import json
from enum import StrEnum
from typing import Any

from signal_harness.agent_integration.context_builder import CONTEXT_PACKET_VERSION
from signal_harness.signal.schemas import NoiseAssessment, SignalCluster, SignalEvent

HARNESS_EVAL_VERSION = "harness-ablation-v1"
PROMPT_VERSION = "agent-prompts-v1"


class HarnessVariant(StrEnum):
    """Analyzer harnesses that can be compared on identical frozen inputs."""

    FIVE_AGENT = "five-agent"
    DETERMINISTIC_SUPERVISOR = "deterministic-supervisor"
    DETERMINISTIC_SUPERVISOR_DEFERRED_LEARNING = "deterministic-supervisor-deferred-learning"
    DETERMINISTIC_EVIDENCE_RESOLVER = "deterministic-evidence-resolver"
    DETERMINISTIC_EVIDENCE_IMPACT_ACTION = "deterministic-evidence-impact-action"
    SELECTIVE_EVIDENCE_RESEARCHER = "selective-evidence-researcher"
    SELECTIVE_EVIDENCE_IMPACT_ACTION = "selective-evidence-impact-action"
    SELECTIVE_EVIDENCE_IMPACT_ACTION_VERIFIER = "selective-evidence-impact-action-verifier"

    @property
    def analyzer_version(self) -> str:
        return {
            self.FIVE_AGENT: "agent-team-v1",
            self.DETERMINISTIC_SUPERVISOR: "deterministic-router-agent-team-v1",
            self.DETERMINISTIC_SUPERVISOR_DEFERRED_LEARNING: "deterministic-router-deferred-learning-v1",
            self.DETERMINISTIC_EVIDENCE_RESOLVER: "deterministic-evidence-resolver-v1",
            self.DETERMINISTIC_EVIDENCE_IMPACT_ACTION: "deterministic-evidence-impact-action-v1",
            self.SELECTIVE_EVIDENCE_RESEARCHER: "selective-evidence-researcher-v1",
            self.SELECTIVE_EVIDENCE_IMPACT_ACTION: "selective-evidence-impact-action-v1",
            self.SELECTIVE_EVIDENCE_IMPACT_ACTION_VERIFIER: "selective-evidence-impact-action-verifier-v1",
        }[self]


def analysis_input_fingerprint(
    *,
    events: list[SignalEvent],
    project_profile: dict[str, Any],
    policy: dict[str, Any],
    noise_assessments: list[NoiseAssessment],
    clusters: list[SignalCluster],
) -> str:
    """Hash the semantic analyzer input while excluding run-volatile metadata."""

    payload = {
        "events": [item.model_dump(mode="json") for item in events],
        "project_profile": project_profile,
        "policy": policy,
        "noise_assessments": [item.model_dump(mode="json") for item in noise_assessments],
        "clusters": [item.model_dump(mode="json") for item in clusters],
    }
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def version_metadata(*, variant: HarnessVariant, policy: dict[str, Any]) -> dict[str, str]:
    return {
        "harness_eval_version": HARNESS_EVAL_VERSION,
        "harness_variant": variant.value,
        "analyzer_version": variant.analyzer_version,
        "prompt_version": PROMPT_VERSION,
        "context_packet_version": CONTEXT_PACKET_VERSION,
        "policy_version": str(policy.get("version") or "unknown"),
    }
