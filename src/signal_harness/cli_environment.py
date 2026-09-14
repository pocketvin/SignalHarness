"""Current product CLI over the shared EnvironmentApplication contract."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Literal, cast

import typer
from dotenv import load_dotenv

from signal_harness.environment_application import EnvironmentApplication
from signal_harness.projects.catalog import default_project_id
from signal_harness.resources import resolve_config_dir
from signal_harness.runtime.windows import WindowMode


def _resolve(root: Path, value: Path) -> Path:
    return value.expanduser().resolve() if value.is_absolute() else (root / value).resolve()


def _application(
    *,
    root: Path,
    config_dir: Path,
    state_dir: Path,
    output_dir: Path,
) -> EnvironmentApplication:
    return EnvironmentApplication(
        cwd=root,
        config_dir=resolve_config_dir(root, config_dir),
        state_dir=_resolve(root, state_dir),
        output_dir=_resolve(root, output_dir),
    )


def _project_id(app: EnvironmentApplication, project: str | None) -> str:
    return project or default_project_id(app.config_dir)


def _json(value: object) -> None:
    typer.echo(json.dumps(value, ensure_ascii=False, indent=2))


def register_environment_commands(app: typer.Typer) -> None:
    @app.command("environment")
    def environment(
        project: str | None = typer.Option(None, "--project", help="Connected project ID"),
        window: str = typer.Option("since_last", "--window", help="since_last, 24h, 7d, or 30d"),
        cwd: Path = typer.Option(Path("."), "--cwd"),
        config_dir: Path = typer.Option(Path("configs"), "--config-dir"),
        state_dir: Path = typer.Option(Path(".signal-harness"), "--state-dir"),
        output_dir: Path = typer.Option(Path("outputs"), "--output-dir"),
    ) -> None:
        """Run the current Project Environment Intelligence pipeline."""
        root = cwd.expanduser().resolve()
        load_dotenv(root / ".env", override=False)
        if window not in {"since_last", "24h", "7d", "30d"}:
            raise typer.BadParameter("window must be since_last, 24h, 7d, or 30d")
        application = _application(
            root=root,
            config_dir=config_dir,
            state_dir=state_dir,
            output_dir=output_dir,
        )
        selected = _project_id(application, project)
        try:
            report = asyncio.run(
                application.run_scan(
                    selected,
                    window=cast(WindowMode, window),
                    progress_listener=lambda progress: typer.echo(progress.message, err=True),
                )
            )
        except (RuntimeError, ValueError) as exc:
            raise typer.BadParameter(str(exc)) from exc
        _json(report)

    @app.command("environment-context")
    def environment_context(
        project: str | None = typer.Option(None, "--project"),
        cwd: Path = typer.Option(Path("."), "--cwd"),
        config_dir: Path = typer.Option(Path("configs"), "--config-dir"),
        state_dir: Path = typer.Option(Path(".signal-harness"), "--state-dir"),
    ) -> None:
        """Read the effective Project Profile used by future environment scans."""
        root = cwd.expanduser().resolve()
        application = _application(
            root=root,
            config_dir=config_dir,
            state_dir=state_dir,
            output_dir=Path("outputs"),
        )
        try:
            _json(application.profile_snapshot(_project_id(application, project)))
        except ValueError as exc:
            raise typer.BadParameter(str(exc)) from exc

    @app.command("environment-architecture")
    def environment_architecture(
        project: str | None = typer.Option(None, "--project"),
        cwd: Path = typer.Option(Path("."), "--cwd"),
        config_dir: Path = typer.Option(Path("configs"), "--config-dir"),
        state_dir: Path = typer.Option(Path(".signal-harness"), "--state-dir"),
    ) -> None:
        """Read the current evidence-backed static Architecture Snapshot."""
        root = cwd.expanduser().resolve()
        application = _application(
            root=root,
            config_dir=config_dir,
            state_dir=state_dir,
            output_dir=Path("outputs"),
        )
        try:
            _json(application.architecture(_project_id(application, project)))
        except ValueError as exc:
            raise typer.BadParameter(str(exc)) from exc

    @app.command("environment-report")
    def environment_report(
        project: str | None = typer.Option(None, "--project"),
        scan: str | None = typer.Option(None, "--scan", help="Saved scan id; latest when omitted"),
        cwd: Path = typer.Option(Path("."), "--cwd"),
        config_dir: Path = typer.Option(Path("configs"), "--config-dir"),
        state_dir: Path = typer.Option(Path(".signal-harness"), "--state-dir"),
    ) -> None:
        """Read one saved Direction-first report without running a model."""
        root = cwd.expanduser().resolve()
        application = _application(
            root=root,
            config_dir=config_dir,
            state_dir=state_dir,
            output_dir=Path("outputs"),
        )
        selected = _project_id(application, project)
        try:
            _json(application.require_report(selected, scan))
        except ValueError as exc:
            raise typer.BadParameter(str(exc)) from exc

    @app.command("environment-changes")
    def environment_changes(
        project: str | None = typer.Option(None, "--project"),
        scan: str | None = typer.Option(None, "--scan"),
        view: Literal["all", "relevant", "featured", "activity", "unavailable"] = typer.Option(
            "relevant", "--view"
        ),
        query: str = typer.Option("", "--query"),
        direction: str | None = typer.Option(None, "--direction"),
        offset: int = typer.Option(0, "--offset", min=0),
        limit: int = typer.Option(25, "--limit", min=1, max=100),
        cwd: Path = typer.Option(Path("."), "--cwd"),
        config_dir: Path = typer.Option(Path("configs"), "--config-dir"),
        state_dir: Path = typer.Option(Path(".signal-harness"), "--state-dir"),
    ) -> None:
        """List the same Change projection used by the Web environment workspace."""
        root = cwd.expanduser().resolve()
        application = _application(
            root=root,
            config_dir=config_dir,
            state_dir=state_dir,
            output_dir=Path("outputs"),
        )
        selected = _project_id(application, project)
        try:
            report = application.require_report(selected, scan)
            _json(
                application.changes(
                    selected,
                    str(report["scan_id"]),
                    view=view,
                    query=query,
                    direction_id=direction,
                    offset=offset,
                    limit=limit,
                )
            )
        except ValueError as exc:
            raise typer.BadParameter(str(exc)) from exc

    @app.command("environment-change")
    def environment_change(
        change_id: str = typer.Argument(...),
        project: str | None = typer.Option(None, "--project"),
        scan: str | None = typer.Option(None, "--scan"),
        cwd: Path = typer.Option(Path("."), "--cwd"),
        config_dir: Path = typer.Option(Path("configs"), "--config-dir"),
        state_dir: Path = typer.Option(Path(".signal-harness"), "--state-dir"),
    ) -> None:
        """Read one frozen environment Change from the current product model."""
        root = cwd.expanduser().resolve()
        application = _application(
            root=root,
            config_dir=config_dir,
            state_dir=state_dir,
            output_dir=Path("outputs"),
        )
        selected = _project_id(application, project)
        try:
            report = application.require_report(selected, scan)
            _json(application.change(selected, str(report["scan_id"]), change_id))
        except ValueError as exc:
            raise typer.BadParameter(str(exc)) from exc

    @app.command("environment-feedback")
    def environment_feedback(
        change_id: str = typer.Argument(...),
        label: Literal["useful", "not_useful", "false_positive", "too_generic"] = typer.Option(
            ..., "--label"
        ),
        note: str = typer.Option("", "--note"),
        project: str | None = typer.Option(None, "--project"),
        scan: str | None = typer.Option(None, "--scan"),
        cwd: Path = typer.Option(Path("."), "--cwd"),
        config_dir: Path = typer.Option(Path("configs"), "--config-dir"),
        state_dir: Path = typer.Option(Path(".signal-harness"), "--state-dir"),
    ) -> None:
        """Attach review-first learning feedback to one frozen environment Change."""
        root = cwd.expanduser().resolve()
        application = _application(
            root=root,
            config_dir=config_dir,
            state_dir=state_dir,
            output_dir=Path("outputs"),
        )
        selected = _project_id(application, project)
        try:
            report = application.require_report(selected, scan)
            _json(
                application.record_feedback(
                    selected,
                    str(report["scan_id"]),
                    change_id,
                    label=label,
                    note=note,
                    source="environment-cli",
                )
            )
        except (PermissionError, ValueError) as exc:
            raise typer.BadParameter(str(exc)) from exc

    @app.command("environment-outcome")
    def environment_outcome(
        change_id: str = typer.Argument(...),
        impact: Literal["yes", "no"] = typer.Option(..., "--impact"),
        note: str = typer.Option("", "--note"),
        project: str | None = typer.Option(None, "--project"),
        scan: str | None = typer.Option(None, "--scan"),
        cwd: Path = typer.Option(Path("."), "--cwd"),
        config_dir: Path = typer.Option(Path("configs"), "--config-dir"),
        state_dir: Path = typer.Option(Path(".signal-harness"), "--state-dir"),
    ) -> None:
        """Record an observed impact outcome without rewriting the historical report."""
        root = cwd.expanduser().resolve()
        application = _application(
            root=root,
            config_dir=config_dir,
            state_dir=state_dir,
            output_dir=Path("outputs"),
        )
        selected = _project_id(application, project)
        try:
            report = application.require_report(selected, scan)
            _json(
                application.record_outcome(
                    selected,
                    str(report["scan_id"]),
                    change_id,
                    impact_observed=impact == "yes",
                    note=note,
                    source="environment-cli",
                )
            )
        except (PermissionError, ValueError) as exc:
            raise typer.BadParameter(str(exc)) from exc

    @app.command("environment-calibration")
    def environment_calibration(
        project: str | None = typer.Option(None, "--project"),
        episodes: bool = typer.Option(False, "--episodes"),
        cwd: Path = typer.Option(Path("."), "--cwd"),
        config_dir: Path = typer.Option(Path("configs"), "--config-dir"),
        state_dir: Path = typer.Option(Path(".signal-harness"), "--state-dir"),
    ) -> None:
        """Read feedback/outcome replay readiness for the current product domain."""
        root = cwd.expanduser().resolve()
        application = _application(
            root=root,
            config_dir=config_dir,
            state_dir=state_dir,
            output_dir=Path("outputs"),
        )
        try:
            _json(
                application.calibration_status(
                    _project_id(application, project), include_episodes=episodes
                )
            )
        except (PermissionError, ValueError) as exc:
            raise typer.BadParameter(str(exc)) from exc
