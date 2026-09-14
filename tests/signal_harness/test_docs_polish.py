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


def test_public_ci_stays_offline_and_focused(project_root: Path) -> None:
    ci = (project_root / ".github/workflows/ci.yml").read_text(encoding="utf-8")

    assert 'python-version: "3.11"' in ci
    assert "python -m pytest tests/signal_harness -q" in ci
    assert "ruff check src/signal_harness tests/signal_harness" in ci
    assert "mypy src/signal_harness" in ci
    assert "uv build" in ci
    assert "LLM_API_KEY" not in ci
    assert "--mode agent" not in ci
    assert "signal-harness scan" not in ci
    assert "signal-harness calibrate" not in ci
    assert "regression-eval" not in ci
    assert "project-eval" not in ci


def test_agent_runner_split_modules_exist(project_root: Path) -> None:
    integration = project_root / "src/signal_harness/agent_integration"

    for module in ("invoker.py", "tool_loop.py", "repair.py", "scoring_bridge.py"):
        assert (integration / module).exists()

    runner = (integration / "runner.py").read_text(encoding="utf-8")
    assert "from signal_harness.agent_integration.invoker import" in runner
    assert "from signal_harness.agent_integration.tool_loop import" in runner
    assert "from signal_harness.agent_integration.repair import" in runner
    assert "from signal_harness.agent_integration.scoring_bridge import" in runner


def test_model_eval_matrix_uses_stable_result_labels(project_root: Path) -> None:
    script = (project_root / "scripts/model_eval_matrix.sh").read_text(encoding="utf-8")

    assert "complete_stable" in script
    assert "complete_unstable" in script
    assert "schema_valid_rate >= 0.99" in script
    assert "fallback_rate == 0" in script
    assert "retry_rate == 0" in script
    assert "timeout_count == 0" in script
    assert "total_tool_error_count == 0" in script
    assert "No stable provider on this fixture" in script


def test_public_readmes_describe_current_product_in_both_languages(project_root: Path) -> None:
    zh = (project_root / "README.md").read_text(encoding="utf-8")
    en = (project_root / "README.en.md").read_text(encoding="utf-8")

    assert "Project Environment Intelligence" in zh
    assert "README.en.md" in zh
    assert "Project Environment Intelligence" in en
    assert "README.md" in en
    assert "deterministic workflow + bounded model stages" in zh
    assert "deterministic workflow + bounded model stages" in en
    assert "docs/REPAIR_PASS.md" in zh
    assert "docs/MODEL_EVAL_RESULTS.md" in zh
    assert "docs/REAL_SOURCE_SMOKE.md" in zh


def test_provider_env_example_matches_current_catalog_contract(project_root: Path) -> None:
    env = (project_root / ".env.example").read_text(encoding="utf-8")
    for prefix in ("OPENAI", "QWEN", "KIMI", "DEEPSEEK"):
        assert f"{prefix}_API_KEY=" in env
        assert f"{prefix}_BASE_URL=" in env
        assert f"\n{prefix}_KEY=" not in env


def test_public_product_copy_no_longer_claims_legacy_multi_agent_or_read_only_mcp(
    project_root: Path,
) -> None:
    cli = (project_root / "src/signal_harness/cli.py").read_text(encoding="utf-8")
    service = (project_root / "src/signal_harness/service.py").read_text(encoding="utf-8")
    project = (project_root / "configs/projects/signalharness.yaml").read_text(encoding="utf-8")

    assert "Project Environment Intelligence for software engineering changes." in cli
    assert "read-only context and traces over MCP stdio" not in cli
    assert "read-only MCP HTTP transport" not in service
    assert "Project Environment Intelligence" in project
    assert "Multi-Agent Harness / Eval / Tooling Platform" not in project
