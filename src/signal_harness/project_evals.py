"""Deterministic cross-project context evaluation."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from signal_harness.agents.classifier import ClassifierAgent
from signal_harness.projects.catalog import project_option
from signal_harness.signal.normalizer import normalize_event
from signal_harness.signal.policy import decision_for_score, load_signal_policy, load_yaml_mapping
from signal_harness.signal.scorer import score_signal


@dataclass(frozen=True)
class ProjectContextEvalSummary:
    suite: str
    cases: int
    passed_cases: int
    passed: bool
    results: list[dict[str, Any]]


def evaluate_project_context_suite(
    path: str | Path, *, config_dir: str | Path
) -> ProjectContextEvalSummary:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or not isinstance(payload.get("cases"), list):
        raise ValueError("Project context eval must contain a cases list")
    config = Path(config_dir).expanduser().resolve()
    policy = load_signal_policy(config / "signal_policy.yaml")
    results: list[dict[str, Any]] = []
    classifier = ClassifierAgent()
    for raw_case in payload["cases"]:
        if not isinstance(raw_case, dict):
            raise ValueError("Project context eval case must be a mapping")
        event = normalize_event(dict(raw_case["event"]))
        higher_id = str(raw_case["higher_project"])
        lower_id = str(raw_case["lower_project"])
        min_gap = float(raw_case.get("min_score_gap", 5.0))
        scores: dict[str, float] = {}
        decisions: dict[str, str] = {}
        categories: dict[str, str] = {}
        for project_id in (higher_id, lower_id):
            project = project_option(project_id, config)
            profile = load_yaml_mapping(project.project_profile_path)
            category = classifier.run(event, profile).category
            breakdown = score_signal(event, profile, policy, category=category)
            scores[project_id] = breakdown.final_score
            decisions[project_id] = decision_for_score(breakdown.final_score, policy).value
            categories[project_id] = category.value
        gap = round(scores[higher_id] - scores[lower_id], 2)
        case_passed = gap >= min_gap
        results.append(
            {
                "id": str(raw_case.get("id") or event.event_id),
                "higher_project": higher_id,
                "lower_project": lower_id,
                "scores": scores,
                "decisions": decisions,
                "categories": categories,
                "score_gap": gap,
                "min_score_gap": min_gap,
                "passed": case_passed,
            }
        )
    passed_cases = sum(1 for item in results if item["passed"])
    return ProjectContextEvalSummary(
        suite=str(payload.get("suite") or "project-context-v1"),
        cases=len(results),
        passed_cases=passed_cases,
        passed=passed_cases == len(results),
        results=results,
    )
