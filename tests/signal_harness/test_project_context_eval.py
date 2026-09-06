from pathlib import Path

from signal_harness.project_evals import evaluate_project_context_suite


def test_cross_project_context_eval_passes(project_root: Path) -> None:
    summary = evaluate_project_context_suite(
        project_root / "examples/signal_harness/project_context_eval.json",
        config_dir=project_root / "configs",
    )
    assert summary.suite == "project-context-v1"
    assert summary.cases == 3
    assert summary.passed_cases == 3
    assert summary.passed is True
    assert all(item["score_gap"] >= item["min_score_gap"] for item in summary.results)
