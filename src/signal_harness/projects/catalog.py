"""Project-scoped profile and Watchlist catalog."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


@dataclass(frozen=True)
class ProjectOption:
    id: str
    name: str
    description: str
    project_profile_path: Path
    watchlist_path: Path
    is_default: bool = False

    def public_payload(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "watchlist": _watchlist_metadata(self.watchlist_path),
        }


def project_catalog(config_dir: str | Path) -> list[ProjectOption]:
    root = Path(config_dir).expanduser().resolve()
    project_dir = root / "projects"
    options: list[ProjectOption] = []
    if project_dir.is_dir():
        for path in sorted(project_dir.glob("*.yaml")):
            options.append(_load_project_option(path, root))
    if not options:
        options.append(
            ProjectOption(
                id="signalharness",
                name="SignalHarness",
                description="Default SignalHarness project profile.",
                project_profile_path=root / "project_profile.yaml",
                watchlist_path=root / "watchlist.yaml",
                is_default=True,
            )
        )
    ids = [item.id for item in options]
    if len(ids) != len(set(ids)):
        raise ValueError("Project catalog contains duplicate ids")
    return options


def default_project_id(config_dir: str | Path) -> str:
    options = project_catalog(config_dir)
    return next((item.id for item in options if item.is_default), options[0].id)


def project_option(project_id: str, config_dir: str | Path) -> ProjectOption:
    for item in project_catalog(config_dir):
        if item.id == project_id:
            return item
    raise ValueError(f"Unknown project id: {project_id}")


def _load_project_option(path: Path, config_root: Path) -> ProjectOption:
    try:
        payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise ValueError(f"Invalid project catalog entry: {path.name}") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"Project catalog entry must be a mapping: {path.name}")
    project_id = str(payload.get("id") or path.stem).strip()
    name = str(payload.get("name") or project_id).strip()
    if not project_id or not name:
        raise ValueError(f"Project catalog entry requires id and name: {path.name}")
    profile_path = _resolve_catalog_path(
        path.parent,
        payload.get("project_profile"),
        config_root,
    )
    watchlist_path = _resolve_catalog_path(
        path.parent,
        payload.get("watchlist"),
        config_root,
    )
    return ProjectOption(
        id=project_id,
        name=name,
        description=str(payload.get("description") or "").strip(),
        project_profile_path=profile_path,
        watchlist_path=watchlist_path,
        is_default=bool(payload.get("default", False)),
    )


def _resolve_catalog_path(base: Path, value: Any, config_root: Path) -> Path:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("Project catalog entry requires profile and watchlist paths")
    target = (base / value).resolve()
    try:
        target.relative_to(config_root)
    except ValueError as exc:
        raise ValueError("Project config paths must stay inside config_dir") from exc
    if not target.is_file():
        raise ValueError(f"Project config file does not exist: {target.name}")
    return target


def _watchlist_metadata(path: Path) -> dict[str, Any]:
    try:
        payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError):
        return {"source_count": 0, "sources": []}
    if not isinstance(payload, dict):
        return {"source_count": 0, "sources": []}
    sources: list[dict[str, str]] = []
    github = payload.get("github")
    if isinstance(github, dict):
        repos = github.get("repositories")
        if isinstance(repos, list):
            for item in repos:
                if isinstance(item, dict) and item.get("repo"):
                    sources.append({"type": "github", "name": str(item["repo"])})
    local_git = payload.get("local_git")
    if isinstance(local_git, dict):
        repositories = local_git.get("repositories")
        if isinstance(repositories, list):
            for item in repositories:
                if isinstance(item, dict) and (item.get("name") or item.get("path")):
                    sources.append(
                        {"type": "local_git", "name": str(item.get("name") or item.get("path"))}
                    )
    package_registries = payload.get("package_registries")
    if isinstance(package_registries, dict):
        pypi = package_registries.get("pypi")
        if isinstance(pypi, dict):
            packages = pypi.get("packages")
            if isinstance(packages, list):
                for item in packages:
                    if isinstance(item, dict) and item.get("name"):
                        sources.append({"type": "pypi", "name": str(item["name"])})
                    elif isinstance(item, str) and item.strip():
                        sources.append({"type": "pypi", "name": item.strip()})
    security = payload.get("security")
    if isinstance(security, dict) and isinstance(security.get("osv"), dict):
        if bool(security["osv"].get("enabled", False)):
            sources.append({"type": "security", "name": "OSV"})
    rss = payload.get("rss")
    if isinstance(rss, dict):
        feeds = rss.get("feeds")
        if isinstance(feeds, list):
            for item in feeds:
                if isinstance(item, dict) and item.get("name"):
                    sources.append({"type": "rss", "name": str(item["name"])})
    web_changes = payload.get("web_changes")
    if isinstance(web_changes, dict):
        web_sources = web_changes.get("sources")
        if isinstance(web_sources, list):
            for item in web_sources:
                if isinstance(item, dict) and item.get("name"):
                    sources.append({"type": "web", "name": str(item["name"])})
    return {"source_count": len(sources), "sources": sources}
