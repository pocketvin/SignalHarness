"""Deterministic project onboarding drafts from local manifests or browser-safe bundles."""

from __future__ import annotations

import json
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - Python 3.10 compatibility
    import tomli as tomllib  # type: ignore[import-not-found,no-redef]

from signal_harness.signal.source_identity import github_repository_from_remote
from signal_harness.utils.fs import atomic_write_text

_MAX_MANIFEST_BYTES = 1_000_000
_ALLOWED_MANIFESTS = {
    "pyproject.toml",
    "package.json",
    "requirements.txt",
    "requirements-dev.txt",
    "requirements.in",
    "cargo.toml",
    "go.mod",
    "uv.lock",
    "package-lock.json",
}
_IGNORED_DIRS = {
    ".git",
    ".venv",
    "venv",
    "node_modules",
    "dist",
    "build",
    "coverage",
    ".next",
    "target",
}


class ProjectManifest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    path: str = Field(min_length=1, max_length=240)
    content: str = Field(max_length=_MAX_MANIFEST_BYTES)

    @field_validator("path")
    @classmethod
    def _safe_relative_path(cls, value: str) -> str:
        normalized = value.replace("\\", "/")
        while normalized.startswith("./"):
            normalized = normalized[2:]
        candidate = Path(normalized)
        if candidate.is_absolute() or ".." in candidate.parts:
            raise ValueError("manifest path must be relative")
        return normalized


class ProjectDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    name: str
    description: str
    project_profile: dict[str, Any]
    watchlist: dict[str, Any]
    evidence_files: list[str]
    detected_paths: list[str]
    review_required: bool = False
    generated_at: datetime


_PYPI_DEPENDENCIES = {
    "fastapi", "pydantic", "httpx", "uvicorn", "openai", "langgraph", "mcp", "typer",
}
_NPM_DEPENDENCIES = {
    "react", "next", "vite", "typescript", "zod", "fastify", "express",
    "@modelcontextprotocol/sdk",
}


def _package_registry_for_dependency(name: str) -> str | None:
    if name in _PYPI_DEPENDENCIES:
        return "pypi"
    if name in _NPM_DEPENDENCIES:
        return "npm"
    return None


_DEPENDENCY_MAP: dict[str, dict[str, Any]] = {
    "fastapi": {
        "label": "FastAPI",
        "repo": "fastapi/fastapi",
        "keywords": ["ASGI", "FastAPI", "API compatibility"],
        "modules": ["API service", "request validation"],
    },
    "pydantic": {
        "label": "Pydantic",
        "repo": "pydantic/pydantic",
        "keywords": ["Pydantic", "JSON schema", "validation"],
        "modules": ["structured output", "schema validation"],
    },
    "httpx": {
        "label": "httpx",
        "repo": "encode/httpx",
        "keywords": ["httpx", "HTTP client", "asyncio"],
        "modules": ["HTTP client", "async runtime"],
    },
    "uvicorn": {
        "label": "Uvicorn",
        "repo": "encode/uvicorn",
        "keywords": ["Uvicorn", "ASGI", "event loop"],
        "modules": ["API runtime"],
    },
    "openai": {
        "label": "OpenAI Python SDK",
        "repo": "openai/openai-python",
        "keywords": ["OpenAI API", "structured output", "provider API"],
        "modules": ["provider adapter"],
        "web": "https://developers.openai.com/api/docs/changelog",
        "web_entity_type": "provider",
        "web_entity_name": "OpenAI API",
    },
    "langgraph": {
        "label": "LangGraph",
        "repo": "langchain-ai/langgraph",
        "keywords": ["LangGraph", "checkpoint", "agent runtime"],
        "modules": ["Agent orchestration", "checkpoint persistence"],
    },
    "mcp": {
        "label": "Model Context Protocol",
        "repo": "modelcontextprotocol/python-sdk",
        "keywords": ["MCP", "tool protocol", "transport"],
        "modules": ["MCP integration"],
        "web": "https://modelcontextprotocol.io/specification/latest",
        "web_entity_type": "protocol",
        "web_entity_name": "Model Context Protocol",
    },
    "typer": {
        "label": "Typer",
        "repo": "fastapi/typer",
        "keywords": ["Typer", "CLI compatibility"],
        "modules": ["CLI"],
    },
    "react": {
        "label": "React",
        "repo": "facebook/react",
        "keywords": ["React", "frontend runtime"],
        "modules": ["frontend"],
    },
    "next": {
        "label": "Next.js",
        "repo": "vercel/next.js",
        "keywords": ["Next.js", "server components", "routing"],
        "modules": ["frontend", "web runtime"],
    },
    "vite": {
        "label": "Vite",
        "repo": "vitejs/vite",
        "keywords": ["Vite", "build tooling"],
        "modules": ["frontend build"],
    },
    "typescript": {
        "label": "TypeScript",
        "repo": "microsoft/TypeScript",
        "keywords": ["TypeScript", "type system"],
        "modules": ["type checking"],
    },
    "zod": {
        "label": "Zod",
        "repo": "colinhacks/zod",
        "keywords": ["Zod", "schema validation"],
        "modules": ["schema validation"],
    },
    "fastify": {
        "label": "Fastify",
        "repo": "fastify/fastify",
        "keywords": ["Fastify", "Node.js API"],
        "modules": ["API service"],
    },
    "express": {
        "label": "Express",
        "repo": "expressjs/express",
        "keywords": ["Express", "Node.js API"],
        "modules": ["API service"],
    },
    "@modelcontextprotocol/sdk": {
        "label": "Model Context Protocol",
        "repo": "modelcontextprotocol/typescript-sdk",
        "keywords": ["MCP", "tool protocol", "transport"],
        "modules": ["MCP integration"],
        "web": "https://modelcontextprotocol.io/specification/latest",
        "web_entity_type": "protocol",
        "web_entity_name": "Model Context Protocol",
    },
}


def inspect_project_directory(project_root: str | Path) -> ProjectDraft:
    root = Path(project_root).expanduser().resolve()
    if not root.is_dir():
        raise ValueError("Project path must be an existing directory")
    manifests: list[ProjectManifest] = []
    candidates = list(root.glob("requirements*.txt"))
    for name in (
        "pyproject.toml", "package.json", "requirements.in", "Cargo.toml", "go.mod",
        "uv.lock", "package-lock.json",
    ):
        candidates.append(root / name)
    seen: set[Path] = set()
    for path in candidates:
        if path in seen or not path.is_file():
            continue
        seen.add(path)
        if path.stat().st_size > _MAX_MANIFEST_BYTES:
            continue
        manifests.append(
            ProjectManifest(
                path=path.relative_to(root).as_posix(),
                content=path.read_text(encoding="utf-8", errors="replace"),
            )
        )
    draft = draft_project(manifests=manifests, paths=_collect_paths(root), name_hint=root.name)
    watchlist = dict(draft.watchlist)
    github_repo = _local_github_repository(root)
    watchlist["local_git"] = {
        "repositories": [
            {
                "name": draft.name,
                "path": str(root),
                **({"github_repo": github_repo} if github_repo else {}),
            }
        ]
    }
    if github_repo:
        github = dict(watchlist.get("github", {}))
        repositories = [dict(item) for item in github.get("repositories", []) if isinstance(item, dict)]
        existing = next((item for item in repositories if item.get("repo") == github_repo), None)
        if existing is None:
            repositories.append(
                {
                    "repo": github_repo,
                    "project_owned": True,
                    "events": ["commits", "pull_requests"],
                }
            )
        else:
            existing["project_owned"] = True
            existing["events"] = list(
                dict.fromkeys([*existing.get("events", []), "commits", "pull_requests"])
            )
        github["repositories"] = repositories
        watchlist["github"] = github
    return draft.model_copy(update={"watchlist": watchlist})


def _local_github_repository(root: Path) -> str | None:
    """Resolve an origin GitHub repo without executing project code."""

    try:
        result = subprocess.run(
            ["git", "-C", str(root), "config", "--get", "remote.origin.url"],
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    return github_repository_from_remote(result.stdout.strip())


def draft_project(
    *, manifests: list[ProjectManifest], paths: list[str], name_hint: str | None = None
) -> ProjectDraft:
    manifest_map = {item.path.lower(): item.content for item in manifests}
    dependencies: set[str] = set()
    tech_stack: list[str] = []
    detected_name = ""

    pyproject = _manifest_by_name(manifest_map, "pyproject.toml")
    if pyproject is not None:
        tech_stack.append("Python")
        parsed = _parse_toml(pyproject)
        project = parsed.get("project", {}) if isinstance(parsed, dict) else {}
        if isinstance(project, dict):
            detected_name = detected_name or str(project.get("name") or "").strip()
            for value in (
                project.get("dependencies", [])
                if isinstance(project.get("dependencies"), list)
                else []
            ):
                dep = _normalize_python_dependency(str(value))
                if dep:
                    dependencies.add(dep)
        poetry = (
            ((parsed.get("tool") or {}).get("poetry") or {}) if isinstance(parsed, dict) else {}
        )
        if isinstance(poetry, dict):
            detected_name = detected_name or str(poetry.get("name") or "").strip()
            deps = poetry.get("dependencies")
            if isinstance(deps, dict):
                dependencies.update(
                    _normalize_package_name(str(key))
                    for key in deps
                    if str(key).lower() != "python"
                )

    package_json = _manifest_by_name(manifest_map, "package.json")
    if package_json is not None:
        tech_stack.append(
            "TypeScript / JavaScript"
            if any(
                path.lower().endswith((".ts", ".tsx")) or "tsconfig" in path.lower()
                for path in paths
            )
            else "JavaScript / Node.js"
        )
        try:
            payload = json.loads(package_json)
        except json.JSONDecodeError:
            payload = {}
        if isinstance(payload, dict):
            detected_name = detected_name or str(payload.get("name") or "").strip()
            for key in ("dependencies", "devDependencies"):
                deps = payload.get(key)
                if isinstance(deps, dict):
                    dependencies.update(_normalize_package_name(str(name)) for name in deps)

    for path, content in manifest_map.items():
        if Path(path).name.startswith("requirements"):
            tech_stack.append("Python")
            dependencies.update(_parse_requirements(content))
        elif Path(path).name.lower() == "cargo.toml":
            tech_stack.append("Rust")
            parsed = _parse_toml(content)
            package = parsed.get("package", {}) if isinstance(parsed, dict) else {}
            if isinstance(package, dict):
                detected_name = detected_name or str(package.get("name") or "").strip()
            deps = parsed.get("dependencies", {}) if isinstance(parsed, dict) else {}
            if isinstance(deps, dict):
                dependencies.update(_normalize_package_name(str(name)) for name in deps)
        elif Path(path).name.lower() == "go.mod":
            tech_stack.append("Go")
            module, go_deps = _parse_go_mod(content)
            detected_name = detected_name or (module.rsplit("/", 1)[-1] if module else "")
            dependencies.update(go_deps)

    dependencies.discard("")
    ordered_deps = sorted(dependencies, key=str.lower)
    known = [_DEPENDENCY_MAP[name] for name in ordered_deps if name in _DEPENDENCY_MAP]
    ecosystems = _unique([str(item["label"]) for item in known])
    focus_keywords = _unique([word for item in known for word in item.get("keywords", [])])
    critical_modules = _unique(
        _path_modules(paths) + [module for item in known for module in item.get("modules", [])]
    )
    repos = _unique([str(item["repo"]) for item in known if item.get("repo")])
    repo_identity: dict[str, tuple[str, str | None]] = {}
    for dependency in ordered_deps:
        item = _DEPENDENCY_MAP.get(dependency)
        if not item or not item.get("repo"):
            continue
        repo_identity[str(item["repo"])] = (
            dependency,
            _package_registry_for_dependency(dependency),
        )
    web_sources: list[dict[str, Any]] = []
    for item in known:
        url = item.get("web")
        if url and not any(existing["url"] == url for existing in web_sources):
            source = {
                "name": f"{item['label']} official page",
                "adapter": "http",
                "url": url,
                "official": True,
            }
            entity_type = str(item.get("web_entity_type") or "").strip()
            entity_name = str(item.get("web_entity_name") or "").strip()
            if entity_type and entity_name:
                source["entity_type"] = entity_type
                source["entity_name"] = entity_name
            web_sources.append(source)

    name = detected_name or (name_hint or "").strip() or "Imported Project"
    purpose = _project_description(manifest_map)
    dependency_evidence = _dependency_evidence(manifest_map, ordered_deps)
    providers = _detected_providers(ordered_deps)
    protocols = _detected_protocols(ordered_deps)
    runtimes = _detected_runtimes(tech_stack, ordered_deps)
    evidence_files = [item.path for item in manifests]
    unknowns = []
    if not purpose:
        unknowns.append("project purpose was not declared in a supported manifest")
    if not providers:
        unknowns.append("external providers/APIs were not deterministically detected")
    project_id = _slugify(name)
    if not project_id:
        project_id = "imported-project"
    profile = {
        "project_name": name,
        "purpose": purpose or f"Software project {name}; purpose not declared in a supported manifest.",
        "goal": purpose or f"Monitor external changes that can affect {name}.",
        "tech_stack": _unique(tech_stack),
        "runtimes": runtimes,
        "protocols": protocols,
        "providers": providers,
        "critical_modules": critical_modules or ["project-wide"],
        "dependencies": ordered_deps,
        "dependency_evidence": dependency_evidence,
        "monitored_ecosystem": ecosystems,
        "competitors": [],
        "focus_keywords": focus_keywords,
        "ignore_keywords": ["consumer giveaway", "cryptocurrency price"],
        "evidence": {
            "manifest_files": evidence_files,
            "detected_paths": paths[:500],
        },
        "unknowns": unknowns,
        "onboarding": {
            "review_required": False,
            "auto_active": True,
            "evidence_files": evidence_files,
        },
    }
    watchlist: dict[str, Any] = {}
    if repos:
        repositories: list[dict[str, Any]] = []
        for repo in repos:
            entry: dict[str, Any] = {"repo": repo, "events": ["releases", "issues"]}
            package_identity = repo_identity.get(repo)
            if package_identity is not None:
                package_name, package_registry = package_identity
                entry["package_name"] = package_name
                if package_registry:
                    entry["package_registry"] = package_registry
            repositories.append(entry)
        watchlist["github"] = {"repositories": repositories}
        watchlist["rss"] = {
            "feeds": [
                {
                    "name": "GitHub Changelog",
                    "official": True,
                    "url": "https://github.blog/changelog/feed/",
                }
            ]
        }
    if web_sources:
        watchlist["web_changes"] = {"sources": web_sources}
    if any(
        str(item.get("resolved_version") or "").strip()
        and str(item.get("ecosystem") or "") in {"PyPI", "npm"}
        for item in dependency_evidence
    ):
        watchlist["security"] = {"osv": {"enabled": True}}
    return ProjectDraft(
        id=project_id,
        name=name,
        description=f"Onboarding draft for {name}; detected {len(ordered_deps)} dependencies and {len(repos)} monitored repositories.",
        project_profile=profile,
        watchlist=watchlist,
        evidence_files=[item.path for item in manifests],
        detected_paths=paths[:500],
        generated_at=datetime.now(timezone.utc),
    )


def write_project_draft(draft: ProjectDraft, output_dir: str | Path) -> dict[str, Path]:
    root = Path(output_dir).expanduser().resolve() / draft.id
    root.mkdir(parents=True, exist_ok=True)
    paths = {
        "profile": root / "project_profile.yaml",
        "watchlist": root / "watchlist.yaml",
        "project": root / "project.yaml",
        "json": root / "draft.json",
    }
    atomic_write_text(
        paths["profile"], yaml.safe_dump(draft.project_profile, allow_unicode=True, sort_keys=False)
    )
    atomic_write_text(
        paths["watchlist"], yaml.safe_dump(draft.watchlist, allow_unicode=True, sort_keys=False)
    )
    catalog = {
        "id": draft.id,
        "name": draft.name,
        "description": draft.description,
        "project_profile": "../project_profiles/%s.yaml" % draft.id,
        "watchlist": "../watchlists/%s.yaml" % draft.id,
        "default": False,
    }
    atomic_write_text(
        paths["project"], yaml.safe_dump(catalog, allow_unicode=True, sort_keys=False)
    )
    atomic_write_text(paths["json"], draft.model_dump_json(indent=2))
    return paths


def apply_project_draft(
    draft: ProjectDraft, config_dir: str | Path, *, overwrite: bool = False
) -> dict[str, Path]:
    root = Path(config_dir).expanduser().resolve()
    targets = {
        "profile": root / "project_profiles" / f"{draft.id}.yaml",
        "watchlist": root / "watchlists" / f"{draft.id}.yaml",
        "project": root / "projects" / f"{draft.id}.yaml",
    }
    existing = [path for path in targets.values() if path.exists()]
    if existing and not overwrite:
        raise FileExistsError(
            "Project config already exists; use --force only after reviewing the draft"
        )
    for path in targets.values():
        path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(
        targets["profile"],
        yaml.safe_dump(draft.project_profile, allow_unicode=True, sort_keys=False),
    )
    atomic_write_text(
        targets["watchlist"], yaml.safe_dump(draft.watchlist, allow_unicode=True, sort_keys=False)
    )
    catalog = {
        "id": draft.id,
        "name": draft.name,
        "description": draft.description,
        "project_profile": f"../project_profiles/{draft.id}.yaml",
        "watchlist": f"../watchlists/{draft.id}.yaml",
        "default": False,
    }
    atomic_write_text(
        targets["project"], yaml.safe_dump(catalog, allow_unicode=True, sort_keys=False)
    )
    return targets


def _project_description(manifests: dict[str, str]) -> str:
    pyproject = _manifest_by_name(manifests, "pyproject.toml")
    if pyproject is not None:
        parsed = _parse_toml(pyproject)
        project = parsed.get("project", {}) if isinstance(parsed, dict) else {}
        if isinstance(project, dict):
            value = str(project.get("description") or "").strip()
            if value:
                return value
    package_json = _manifest_by_name(manifests, "package.json")
    if package_json is not None:
        try:
            payload = json.loads(package_json)
        except json.JSONDecodeError:
            payload = {}
        if isinstance(payload, dict):
            value = str(payload.get("description") or "").strip()
            if value:
                return value
    return ""


def _dependency_evidence(
    manifests: dict[str, str], dependencies: list[str]
) -> list[dict[str, Any]]:
    declared = _declared_dependency_specs(manifests)
    resolved = _resolved_dependency_versions(manifests)
    result: list[dict[str, Any]] = []
    for name in dependencies:
        declaration = declared.get(name, {})
        resolution = resolved.get(name, {})
        files = _unique([
            *[str(value) for value in declaration.get("source_files", [])],
            *[str(value) for value in resolution.get("source_files", [])],
        ])
        ecosystem = ""
        if any(Path(value).name.lower() in {"package.json", "package-lock.json"} for value in files):
            ecosystem = "npm"
        elif any(
            Path(value).name.lower() in {"pyproject.toml", "uv.lock"}
            or Path(value).name.lower().startswith("requirements")
            for value in files
        ):
            ecosystem = "PyPI"
        result.append(
            {
                "name": name,
                "declared": str(declaration.get("declared") or ""),
                "resolved_version": str(resolution.get("version") or ""),
                "source_files": files,
                "confidence": "verified" if resolution.get("version") else "declared",
                "ecosystem": ecosystem,
            }
        )
    return result


def _declared_dependency_specs(manifests: dict[str, str]) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    pyproject = _manifest_by_name(manifests, "pyproject.toml")
    if pyproject is not None:
        parsed = _parse_toml(pyproject)
        project = parsed.get("project", {}) if isinstance(parsed, dict) else {}
        values = project.get("dependencies", []) if isinstance(project, dict) else []
        if isinstance(values, list):
            for raw in values:
                text = str(raw).strip()
                name = _normalize_python_dependency(text)
                if name:
                    result[name] = {"declared": text, "source_files": ["pyproject.toml"]}
    package_json = _manifest_by_name(manifests, "package.json")
    if package_json is not None:
        try:
            payload = json.loads(package_json)
        except json.JSONDecodeError:
            payload = {}
        if isinstance(payload, dict):
            for key in ("dependencies", "devDependencies"):
                values = payload.get(key)
                if not isinstance(values, dict):
                    continue
                for raw_name, spec in values.items():
                    name = _normalize_package_name(str(raw_name))
                    result[name] = {
                        "declared": str(spec),
                        "source_files": ["package.json"],
                    }
    for path, content in manifests.items():
        if not Path(path).name.startswith("requirements"):
            continue
        for line in content.splitlines():
            raw = line.split("#", 1)[0].strip()
            if not raw or raw.startswith(("-", "git+", "http://", "https://")):
                continue
            name = _normalize_python_dependency(raw)
            if name and name not in result:
                result[name] = {"declared": raw, "source_files": [Path(path).name]}
    return result


def _resolved_dependency_versions(manifests: dict[str, str]) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    uv_lock = _manifest_by_name(manifests, "uv.lock")
    if uv_lock is not None:
        parsed = _parse_toml(uv_lock)
        packages = parsed.get("package", []) if isinstance(parsed, dict) else []
        if isinstance(packages, list):
            for item in packages:
                if not isinstance(item, dict):
                    continue
                name = _normalize_package_name(str(item.get("name") or ""))
                version = str(item.get("version") or "").strip()
                if name and version:
                    result[name] = {"version": version, "source_files": ["uv.lock"]}
    package_lock = _manifest_by_name(manifests, "package-lock.json")
    if package_lock is not None:
        try:
            payload = json.loads(package_lock)
        except json.JSONDecodeError:
            payload = {}
        packages = payload.get("packages", {}) if isinstance(payload, dict) else {}
        if isinstance(packages, dict):
            for path, item in packages.items():
                if not str(path).startswith("node_modules/") or not isinstance(item, dict):
                    continue
                name = _normalize_package_name(str(path)[len("node_modules/"):])
                version = str(item.get("version") or "").strip()
                if name and version:
                    result[name] = {"version": version, "source_files": ["package-lock.json"]}
    return result


def _detected_providers(dependencies: list[str]) -> list[str]:
    values: list[str] = []
    if "openai" in dependencies:
        values.append("OpenAI API")
    return values


def _detected_protocols(dependencies: list[str]) -> list[str]:
    values: list[str] = []
    if "mcp" in dependencies or "@modelcontextprotocol/sdk" in dependencies:
        values.append("Model Context Protocol")
    return values


def _detected_runtimes(tech_stack: list[str], dependencies: list[str]) -> list[str]:
    values = list(tech_stack)
    if "fastapi" in dependencies or "uvicorn" in dependencies:
        values.append("ASGI")
    if any(name in dependencies for name in ("fastify", "express", "next")):
        values.append("Node.js")
    return _unique(values)


def _collect_paths(root: Path) -> list[str]:
    result: list[str] = []
    for child in sorted(root.iterdir(), key=lambda item: item.name.lower()):
        if child.name in _IGNORED_DIRS or child.name.startswith("."):
            continue
        result.append(child.relative_to(root).as_posix())
        if child.is_dir():
            try:
                grandchildren = sorted(child.iterdir(), key=lambda item: item.name.lower())[:80]
            except OSError:
                continue
            for item in grandchildren:
                if item.name in _IGNORED_DIRS or item.name.startswith("."):
                    continue
                result.append(item.relative_to(root).as_posix())
        if len(result) >= 500:
            break
    return result


def _manifest_by_name(manifests: dict[str, str], name: str) -> str | None:
    target = name.lower()
    return next(
        (content for path, content in manifests.items() if Path(path).name.lower() == target), None
    )


def _parse_toml(content: str) -> dict[str, Any]:
    try:
        payload = tomllib.loads(content)
    except (ValueError, tomllib.TOMLDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _parse_requirements(content: str) -> set[str]:
    result: set[str] = set()
    for line in content.splitlines():
        raw = line.split("#", 1)[0].strip()
        if not raw or raw.startswith(("-", "git+", "http://", "https://")):
            continue
        name = re.split(r"[<>=!~;\[]", raw, maxsplit=1)[0]
        normalized = _normalize_package_name(name)
        if normalized:
            result.add(normalized)
    return result


def _normalize_python_dependency(value: str) -> str:
    return _normalize_package_name(re.split(r"[<>=!~;\[]", value, maxsplit=1)[0])


def _normalize_package_name(value: str) -> str:
    raw = value.strip().lower()
    if not raw:
        return ""
    if raw.startswith("@") and "/" in raw:
        return raw
    return re.sub(r"[-_.]+", "-", raw)


def _parse_go_mod(content: str) -> tuple[str, set[str]]:
    module = ""
    deps: set[str] = set()
    in_require = False
    for line in content.splitlines():
        stripped = line.strip()
        if stripped.startswith("module "):
            module = stripped.split(None, 1)[1].strip()
        elif stripped == "require (":
            in_require = True
        elif in_require and stripped == ")":
            in_require = False
        elif stripped.startswith("require "):
            parts = stripped.split()
            if len(parts) >= 2:
                deps.add(parts[1].lower())
        elif in_require and stripped and not stripped.startswith("//"):
            deps.add(stripped.split()[0].lower())
    return module, deps


def _path_modules(paths: list[str]) -> list[str]:
    joined = " ".join(paths).lower()
    mappings = [
        ("agent", "Agent orchestration"),
        ("provider", "provider adapter"),
        ("mcp", "MCP integration"),
        ("api", "API service"),
        ("route", "API routing"),
        ("ui", "frontend"),
        ("frontend", "frontend"),
        ("test", "testing"),
        ("eval", "evaluation"),
        ("auth", "authentication"),
        ("migration", "persistence"),
        ("database", "persistence"),
        ("db", "persistence"),
    ]
    return [label for needle, label in mappings if needle in joined]


def _unique(values: list[str]) -> list[str]:
    return list(dict.fromkeys(value for value in values if value))


def _slugify(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return slug[:64]
