from __future__ import annotations

import json
from pathlib import Path

from openharness.config.settings import Settings
from openharness.plugins.loader import load_plugin
from openharness.skills.loader import load_skill_registry


def test_signal_harness_plugin_is_valid(project_root: Path) -> None:
    plugin_dir = project_root / ".openharness/plugins/signal-harness"
    manifest_path = plugin_dir / ".claude-plugin/plugin.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    assert manifest["name"] == "signal-harness"
    plugin = load_plugin(plugin_dir, {})
    assert plugin is not None
    assert {command.name for command in plugin.commands} >= {
        "signal-harness:radar-scan",
        "signal-harness:radar-feedback",
        "signal-harness:radar-calibrate",
    }
    assert len(plugin.agents) >= 3
    assert {agent.name for agent in plugin.agents} == {
        "signal-harness:signal-supervisor-agent",
        "signal-harness:context-evidence-agent",
        "signal-harness:impact-analyst-agent",
        "signal-harness:action-planner-agent",
        "signal-harness:learning-policy-agent",
    }
    assert "pre_tool_use" in plugin.hooks


def test_project_signal_agents_exist(project_root: Path) -> None:
    agents_dir = project_root / ".agents"
    expected = {
        "signal-supervisor-agent.md",
        "context-evidence-agent.md",
        "impact-analyst-agent.md",
        "action-planner-agent.md",
        "learning-policy-agent.md",
    }

    assert expected == {path.name for path in agents_dir.glob("*.md")}


def test_project_visible_signal_skills_load(project_root: Path) -> None:
    registry = load_skill_registry(
        project_root,
        settings=Settings(allow_project_skills=True),
    )
    names = {skill.name for skill in registry.list_skills()}

    assert {
        "signal-triage",
        "evidence-verification",
        "project-impact-analysis",
        "signal-policy-calibration",
    }.issubset(names)
