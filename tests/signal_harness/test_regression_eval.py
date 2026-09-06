from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from signal_harness.cli import app
from signal_harness.signal.text_semantics import contains_affirmed_term

runner = CliRunner()


def test_negation_semantics_rejects_false_breaking_and_security_terms() -> None:
    assert contains_affirmed_term("breaking API migration", "breaking") is True
    assert contains_affirmed_term("no breaking API change", "breaking") is False
    assert contains_affirmed_term("without migration or security impact", "migration") is False
    assert contains_affirmed_term("without migration or security impact", "security") is False


def test_resume_regression_suite_passes_full_mock_agent_gate(
    project_root: Path,
    tmp_path: Path,
) -> None:
    result = runner.invoke(
        app,
        [
            "regression-eval",
            "--mode",
            "mock-agent",
            "--enforce",
            "--cwd",
            str(project_root),
            "--output-dir",
            str(tmp_path / "outputs"),
            "--state-dir",
            str(tmp_path / "state"),
        ],
    )

    assert result.exit_code == 0, result.output
    summary = json.loads(
        (tmp_path / "outputs/regression_eval_summary.json").read_text(encoding="utf-8")
    )
    assert summary["suite"] == "resume-v1"
    assert summary["case_count"] == 40
    assert summary["assessed_count"] == 40
    assert summary["decision_accuracy"] == 1.0
    assert summary["category_accuracy"] == 1.0
    assert summary["priority_precision"] == 1.0
    assert summary["priority_recall"] == 1.0
    assert summary["false_positive_rate"] == 0.0
    assert summary["false_negative_rate"] == 0.0
    assert summary["passed"] is True
    assert summary["mismatches"] == []

    baseline = json.loads(
        (project_root / "examples/signal_harness/regression_baseline.json").read_text(
            encoding="utf-8"
        )
    )
    assert baseline["suite"] == summary["suite"]
    assert baseline["case_count"] == summary["case_count"]
    for key in (
        "decision_accuracy",
        "category_accuracy",
        "priority_precision",
        "priority_recall",
        "false_positive_rate",
        "false_negative_rate",
        "passed",
    ):
        assert baseline[key] == summary[key]


def test_regression_eval_default_state_is_repeatable(
    project_root: Path,
    tmp_path: Path,
) -> None:
    for index in range(2):
        output = tmp_path / f"outputs-{index}"
        result = runner.invoke(
            app,
            [
                "regression-eval",
                "--mode",
                "mock-agent",
                "--enforce",
                "--cwd",
                str(project_root),
                "--output-dir",
                str(output),
            ],
        )
        assert result.exit_code == 0, result.output
        summary = json.loads((output / "regression_eval_summary.json").read_text(encoding="utf-8"))
        assert summary["decision_accuracy"] == 1.0
        assert summary["priority_recall"] == 1.0
        assert summary["passed"] is True
