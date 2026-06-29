from __future__ import annotations

from pathlib import Path


def test_polish_docs_exist_and_are_linked(project_root: Path) -> None:
    docs = [
        "docs/REPAIR_PASS.md",
        "docs/MODEL_EVAL_RESULTS.md",
        "docs/REAL_SOURCE_SMOKE.md",
    ]
    for doc in docs:
        assert (project_root / doc).exists()

    readme = (project_root / "README.md").read_text(encoding="utf-8")
    assert "docs/REPAIR_PASS.md" in readme
    assert "docs/MODEL_EVAL_RESULTS.md" in readme
    assert "docs/REAL_SOURCE_SMOKE.md" in readme


def test_docs_state_current_repair_and_provider_boundaries(project_root: Path) -> None:
    architecture = (project_root / "docs/LLM_AGENT_ARCHITECTURE.md").read_text(
        encoding="utf-8"
    )
    interview = (project_root / "docs/INTERVIEW_GUIDE.md").read_text(
        encoding="utf-8"
    )
    decisions = (project_root / "docs/ARCHITECTURE_DECISIONS.md").read_text(
        encoding="utf-8"
    )

    assert "Impact→ContextEvidence" in architecture
    assert "Action→Impact" in architecture
    assert "agent_team_run_timeout" in architecture
    assert "not provider-native function calling" in interview
    assert "learning-stage" in interview
    assert "heavy dependency" in decisions.lower()
    assert "SignalHarness uses a focused subset" in decisions
