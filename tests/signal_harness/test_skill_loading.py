from __future__ import annotations

from openharness.config.settings import Settings
from openharness.skills.loader import load_skill_registry


def test_signal_skills_load_through_openharness_loader(project_root) -> None:
    registry = load_skill_registry(
        project_root,
        extra_skill_dirs=[project_root / "src" / "signal_harness" / "skills"],
        settings=Settings(allow_project_skills=False),
    )

    names = {skill.name for skill in registry.list_skills()}
    assert {
        "signal-triage",
        "evidence-verification",
        "project-impact-analysis",
        "signal-policy-calibration",
    }.issubset(names)
