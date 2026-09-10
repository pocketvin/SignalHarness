"""Thin product CLI over the same direction-first workflow used by the Web API."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import cast

import typer
from dotenv import load_dotenv

from signal_harness.agent_integration.mode import RunMode
from signal_harness.persistence import ChangeLedger
from signal_harness.persistence.intelligence import IntelligenceRepository
from signal_harness.projects.catalog import default_project_id, project_option
from signal_harness.projects.state import prepare_project_state
from signal_harness.providers.task_policy import TaskPolicy
from signal_harness.runtime.windows import WindowMode
from signal_harness.runtime.workflow import SignalHarnessWorkflow


def register_environment_commands(app: typer.Typer) -> None:
    @app.command("environment")
    def environment(
        project: str | None = typer.Option(None, "--project", help="Connected project ID"),
        window: str = typer.Option("since_last", "--window", help="since_last, 24h, 7d, or 30d"),
        cwd: Path = typer.Option(Path("."), "--cwd"),
        config_dir: Path = typer.Option(Path("configs"), "--config-dir"),
        state_dir: Path = typer.Option(Path(".signal-harness"), "--state-dir"),
        output_dir: Path = typer.Option(Path("outputs/environment"), "--output-dir"),
    ) -> None:
        """Collect ALL changes, batch-interpret, synthesize directions; never auto-deep-dive."""
        root = cwd.expanduser().resolve()
        config = (root / config_dir).resolve()
        load_dotenv(root / ".env", override=False)
        if window not in {"since_last", "24h", "7d", "30d"}:
            raise typer.BadParameter("window must be since_last, 24h, 7d, or 30d")
        if not TaskPolicy.load(config).providers("synthesis"):
            raise typer.BadParameter("Configure real-provider credentials on the server first.")
        option = project_option(project or default_project_id(config), config)
        state = prepare_project_state(root / state_dir, option.id, migrate_legacy_default=True)
        workflow = SignalHarnessWorkflow(
            cwd=root,
            config_dir=config,
            state_dir=state,
            output_dir=root / output_dir,
            project_profile_path=option.project_profile_path,
            watchlist_path=option.watchlist_path,
            project_id=option.id,
            mode=RunMode.AGENT,
            intelligence_pipeline=True,
            progress_listener=lambda p: typer.echo(p.message, err=True),
        )
        result = asyncio.run(workflow.scan(window_mode=cast(WindowMode, window)))
        report = IntelligenceRepository(workflow.ledger.path).report(result.scan_id)
        import json

        typer.echo(json.dumps(report, ensure_ascii=False, indent=2))

    @app.command("environment-report")
    def environment_report(
        project: str | None = typer.Option(None, "--project"),
        cwd: Path = typer.Option(Path("."), "--cwd"),
        state_dir: Path = typer.Option(Path(".signal-harness"), "--state-dir"),
    ) -> None:
        """Read the latest saved product report without running a model."""
        root = cwd.expanduser().resolve()
        option = project_option(project or default_project_id(root / "configs"), root / "configs")
        state = prepare_project_state(root / state_dir, option.id, migrate_legacy_default=True)
        repo = IntelligenceRepository(ChangeLedger(state / "change_ledger.sqlite3").path)
        import json

        typer.echo(json.dumps(repo.latest_report(option.id), ensure_ascii=False, indent=2))
