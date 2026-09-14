"""Bounded, auditable project architecture snapshot from safe source samples.

This module intentionally does not execute project code and does not claim runtime reachability.
It extracts only static structure that can be tied back to concrete project paths/import lines.
"""

from __future__ import annotations

import json
import os
import posixpath
import re
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

_ARCHITECTURE_VERSION = "project-architecture-v1"
_MAX_SOURCE_BYTES = 96_000
_MAX_SOURCE_FILES = 18
_MAX_REFERENCES_PER_DEPENDENCY = 6
_MAX_DEPENDENCIES = 20
_MAX_EDGES = 40

_SOURCE_EXTENSIONS = {".py", ".ts", ".tsx", ".js", ".jsx", ".go", ".rs", ".java", ".swift"}
_DOC_NAMES = {"readme.md", "readme.mdx", "architecture.md", "design.md"}
_SKIP_PARTS = {
    ".git",
    ".venv",
    "venv",
    "node_modules",
    "vendor",
    "dist",
    "build",
    "coverage",
    ".next",
    "target",
    "outputs",
    "work",
    "private-noai",
    "__pycache__",
}
_SECRET = re.compile(
    r"(?i)(api[_-]?key|authorization|bearer|password|secret|token)\s*[:=]|\b(?:sk-|ghp_|gho_)[A-Za-z0-9_-]{12,}"
)
_IMPORT_PATTERNS = (
    re.compile(r"\bimport\s+(?:type\s+)?(?:[^\n;]+?\s+from\s+)?[\"']([^\"']+)[\"']"),
    re.compile(r"\brequire\(\s*[\"']([^\"']+)[\"']\s*\)"),
    re.compile(r"\bfrom\s+([A-Za-z0-9_\.]+)\s+import\b"),
    re.compile(r"^\s*import\s+([A-Za-z0-9_\.]+)\b", re.M),
    re.compile(r"^\s*import\s+[\"']([^\"']+)[\"']", re.M),
    re.compile(r"^\s*use\s+([A-Za-z0-9_:]+)", re.M),
)


class ArchitectureSourceSample(BaseModel):
    model_config = ConfigDict(extra="forbid")
    path: str = Field(min_length=1, max_length=320)
    content: str = Field(max_length=_MAX_SOURCE_BYTES)

    @field_validator("path")
    @classmethod
    def _safe_relative_path(cls, value: str) -> str:
        normalized = value.replace("\\", "/")
        while normalized.startswith("./"):
            normalized = normalized[2:]
        candidate = Path(normalized)
        if candidate.is_absolute() or ".." in candidate.parts:
            raise ValueError("source sample path must be relative")
        if "private-noai" in {part.casefold() for part in candidate.parts}:
            raise ValueError("source sample path is private")
        return normalized


def architecture_version() -> str:
    return _ARCHITECTURE_VERSION


def _manifest_map(manifests: list[Any]) -> dict[str, str]:
    result: dict[str, str] = {}
    for item in manifests:
        path = str(getattr(item, "path", "") or "")
        content = str(getattr(item, "content", "") or "")
        if path:
            result[path.lower()] = content
    return result


def _manifest_entrypoint_paths(manifests: list[Any]) -> list[str]:
    result: list[str] = []
    mapping = _manifest_map(manifests)
    package = next((value for path, value in mapping.items() if Path(path).name == "package.json"), None)
    if package:
        try:
            payload = json.loads(package)
        except json.JSONDecodeError:
            payload = {}
        if isinstance(payload, dict):
            for key in ("main", "module"):
                value = payload.get(key)
                if isinstance(value, str):
                    result.append(value.lstrip("./"))
            bin_value = payload.get("bin")
            if isinstance(bin_value, str):
                result.append(bin_value.lstrip("./"))
            elif isinstance(bin_value, dict):
                result.extend(str(value).lstrip("./") for value in bin_value.values())
            scripts = payload.get("scripts")
            if isinstance(scripts, dict):
                for command in scripts.values():
                    if not isinstance(command, str):
                        continue
                    result.extend(
                        match.group(0).lstrip("./")
                        for match in re.finditer(
                            r"(?:src|app|server|packages?)/[A-Za-z0-9_./-]+\.(?:ts|tsx|js|jsx)",
                            command,
                        )
                    )
    pyproject = next((value for path, value in mapping.items() if Path(path).name == "pyproject.toml"), None)
    if pyproject:
        # Entry points are usually "package.module:function". Convert only the module part to a
        # conservative likely path; existence is checked by the caller's path inventory.
        for module in re.findall(r"=\s*[\"']([A-Za-z0-9_.]+):[A-Za-z0-9_]+[\"']", pyproject):
            result.append(module.replace(".", "/") + ".py")
    return list(dict.fromkeys(result))


def _source_path(path: str) -> bool:
    candidate = Path(path)
    lowered = {part.casefold() for part in candidate.parts}
    if lowered & _SKIP_PARTS:
        return False
    return candidate.suffix.casefold() in _SOURCE_EXTENSIONS or candidate.name.casefold() in _DOC_NAMES


def _production_source_path(path: str) -> bool:
    if Path(path).suffix.casefold() not in _SOURCE_EXTENSIONS:
        return False
    lower = path.casefold()
    name = Path(path).name.casefold()
    blocked = (
        "/test/",
        "/tests/",
        "/__tests__/",
        "/fixture/",
        "/fixtures/",
        "/example/",
        "/examples/",
        "/scripts/",
        ".test.",
        ".spec.",
        ".suite.",
        "test-support",
        "test-helper",
        "test-helpers",
        "e2e",
        ".mock.",
        "mocks.",
        "/mocks/",
    )
    if any(marker in f"/{lower}" for marker in blocked):
        return False
    if name.endswith(".d.ts") or ".config." in name or name.startswith(("eslint.config", "vite.config", "vitest.config", "playwright.config")):
        return False
    return True


def _path_score(path: str, explicit: set[str]) -> tuple[int, int, str]:
    lower = path.casefold()
    name = Path(path).name.casefold()
    score = 0
    if path in explicit:
        score += 140
    entry_names = ("main.", "index.", "server.", "app.", "cli.", "bootstrap.", "entry.")
    if any(name.startswith(prefix) for prefix in entry_names):
        score += 90
    if lower.startswith("src/") or "/src/" in f"/{lower}":
        score += 35
    if _subsystem_for_path(path):
        score += 25
    depth = path.count("/")
    score -= min(depth, 8) * 2
    return (-score, depth, lower)


def select_architecture_paths(paths: list[str], manifests: list[Any], *, limit: int = _MAX_SOURCE_FILES) -> list[str]:
    """Choose production-biased, subsystem-diverse source files plus at most one overview doc."""
    inventory = list(dict.fromkeys(paths))
    explicit = set(_manifest_entrypoint_paths(manifests))
    production = [path for path in inventory if _production_source_path(path)]
    production.sort(key=lambda value: _path_score(value, explicit))
    selected: list[str] = []

    def add(path: str) -> None:
        if path not in selected and path in production and len(selected) < limit:
            selected.append(path)

    for path in production:
        if path in explicit:
            add(path)
    for path in production:
        if _entrypoint_reason(path):
            add(path)
            if len([item for item in selected if _entrypoint_reason(item)]) >= 4:
                break

    buckets: dict[str, list[str]] = {}
    for path in production:
        subsystem = _subsystem_for_path(path)
        if subsystem:
            buckets.setdefault(subsystem, []).append(path)
    for _round in range(2):
        for name in sorted(buckets):
            candidates = [item for item in buckets[name] if item not in selected]
            if candidates:
                add(candidates[0])
            if len(selected) >= limit:
                break
        if len(selected) >= limit:
            break

    for path in production:
        add(path)
        if len(selected) >= max(0, limit - 1):
            break

    docs = [path for path in inventory if Path(path).name.casefold() in _DOC_NAMES]
    docs.sort(key=lambda path: (path.count("/"), path.casefold()))
    if docs and len(selected) < limit:
        selected.append(docs[0])
    return selected[:limit]


def _read_local_sample(approved: Path, relative: str) -> ArchitectureSourceSample | None:
    candidate = approved / relative
    try:
        if candidate.is_symlink() or not candidate.is_file():
            return None
        resolved = candidate.resolve()
        if not resolved.is_relative_to(approved) or "private-noai" in {
            part.casefold() for part in resolved.parts
        }:
            return None
        if resolved.stat().st_size > _MAX_SOURCE_BYTES:
            return None
        content = resolved.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    return ArchitectureSourceSample(path=relative, content=content)


def architecture_neighbor_paths(
    samples: list[ArchitectureSourceSample], paths: list[str], *, limit: int = 6
) -> list[str]:
    """Expand one static relative-import hop without pretending it is runtime reachability."""
    inventory = {path for path in paths if _production_source_path(path)}
    already = {sample.path for sample in samples}
    result: list[str] = []
    for sample in samples:
        if Path(sample.path).suffix.casefold() not in _SOURCE_EXTENSIONS:
            continue
        for imported, _line_number, _excerpt in _extract_imports(sample.content):
            target = _relative_target(sample.path, imported, inventory)
            if target and target not in already and target not in result:
                result.append(target)
                if len(result) >= limit:
                    return result
    return result


def collect_local_architecture_samples(
    root: Path, paths: list[str], manifests: list[Any], *, limit: int = _MAX_SOURCE_FILES
) -> list[ArchitectureSourceSample]:
    """Read selected production source files, then expand one relative-import hop."""
    approved = root.resolve()
    initial_limit = min(limit, 12)
    selected = select_architecture_paths(paths, manifests, limit=initial_limit)
    samples = [
        sample
        for relative in selected
        if (sample := _read_local_sample(approved, relative)) is not None
    ]
    if len(samples) < limit:
        for relative in architecture_neighbor_paths(samples, paths, limit=limit - len(samples)):
            sample = _read_local_sample(approved, relative)
            if sample is not None:
                samples.append(sample)
    return samples[:limit]


def collect_local_source_paths(root: Path, *, limit: int = 6000) -> list[str]:
    """Inventory source/doc paths without following symlinks or reading file contents."""
    approved = root.resolve()
    result: list[str] = []
    for current, dirs, files in os.walk(approved, followlinks=False):
        current_path = Path(current)
        dirs[:] = sorted(
            name
            for name in dirs
            if not name.startswith(".")
            and name.casefold() not in _SKIP_PARTS
            and not (current_path / name).is_symlink()
        )
        for name in sorted(files):
            path = current_path / name
            if path.is_symlink():
                continue
            try:
                relative = path.relative_to(approved).as_posix()
            except ValueError:
                continue
            if _source_path(relative):
                result.append(relative)
                if len(result) >= limit:
                    return result
    return result


def _subsystem_for_path(path: str) -> str | None:
    lower = path.casefold()
    parts = {part.casefold() for part in Path(path).parts}
    if any(part in parts for part in {"agents", "agent", "orchestration", "orchestrator"}) or "agent-" in lower:
        return "Agent / 执行"
    if any(needle in lower for needle in ("provider", "models/", "/models", "llm")):
        return "模型 / Provider"
    if any(needle in lower for needle in ("/mcp/", "mcp-", "/tools/", "tool-", "skills/")):
        return "工具 / 协议"
    if any(needle in lower for needle in ("/gateway/", "/routes/", "/api/", "/server/", "server.")):
        return "API / 服务"
    if any(needle in lower for needle in ("channel", "discord", "slack", "telegram", "message")):
        return "渠道 / 消息"
    if any(needle in lower for needle in ("storage", "/state/", "session", "database", "/db/", "persist")):
        return "状态 / 存储"
    if any(needle in lower for needle in ("game", "scene", "tutorial", "drawing", "gesture", "input", "order")):
        return "游戏 / 交互"
    if any(needle in lower for needle in ("asset", "sprite", "audio", "art", "texture")):
        return "资源 / 内容"
    if any(needle in lower for needle in ("frontend", "/ui/", "/views/", "component", "/web/")):
        return "前端 / UI"
    if any(needle in lower for needle in ("auth", "security", "permission")):
        return "安全 / 鉴权"
    if any(needle in lower for needle in ("runtime", "engine")):
        return "核心运行时"
    if any(needle in lower for needle in ("config", "setting")):
        return "配置 / 启动"
    return None


def _entrypoint_reason(path: str) -> str | None:
    name = Path(path).name.casefold()
    if name in _DOC_NAMES:
        return None
    if any(name.startswith(prefix) for prefix in ("main.", "index.", "server.", "app.", "cli.", "bootstrap.", "entry.")):
        return "入口命名"
    if any(token in path.casefold() for token in ("/routes/", "/server/", "/cli/")):
        return "入口目录"
    return None


def _extract_imports(content: str) -> list[tuple[str, int, str]]:
    rows: list[tuple[str, int, str]] = []
    lines = content.splitlines()
    for line_number, line in enumerate(lines, start=1):
        if _SECRET.search(line):
            continue
        for pattern in _IMPORT_PATTERNS:
            match = pattern.search(line)
            if match:
                rows.append((match.group(1), line_number, line.strip()[:260]))
                break
    return rows


def _dependency_matches_import(dependency: str, imported: str) -> bool:
    dep = dependency.casefold().replace("_", "-")
    target = imported.casefold().replace("_", "-")
    if target.startswith("."):
        return False
    if dep.startswith("@"):
        return target == dep or target.startswith(dep + "/")
    if "/" in dep:
        return target == dep or target.startswith(dep + "/")
    dep_root = dep.split("/", 1)[0]
    target_root = target.split("/", 1)[0].split(".", 1)[0]
    return target_root == dep_root or target.startswith(dep + "/") or target.startswith(dep + ".")


def _relative_target(source_path: str, imported: str, all_paths: set[str]) -> str | None:
    if not imported.startswith("."):
        return None
    base = posixpath.normpath(posixpath.join(posixpath.dirname(source_path), imported))
    candidates = [
        base,
        *(
            [
                re.sub(r"\.m?js$", ".ts", base),
                re.sub(r"\.m?js$", ".tsx", base),
            ]
            if re.search(r"\.m?js$", base)
            else []
        ),
        *(base + suffix for suffix in (".ts", ".tsx", ".js", ".jsx", ".py", ".go", ".rs", ".java", ".swift")),
        *(f"{base}/index{suffix}" for suffix in (".ts", ".tsx", ".js", ".jsx")),
    ]
    return next((candidate for candidate in candidates if candidate in all_paths), None)


def derive_architecture_snapshot(
    profile: dict[str, Any],
    paths: list[str],
    samples: list[ArchitectureSourceSample],
) -> dict[str, Any]:
    """Produce an evidence-backed static architecture view, never a runtime call graph."""
    source_samples = [sample for sample in samples if Path(sample.path).suffix.casefold() in _SOURCE_EXTENSIONS]
    evidence_paths = [sample.path for sample in samples]
    subsystem_paths: dict[str, list[str]] = {}
    for sample in source_samples:
        subsystem = _subsystem_for_path(sample.path)
        if subsystem:
            subsystem_paths.setdefault(subsystem, []).append(sample.path)
    subsystems = [
        {"name": name, "paths": values[:5], "sample_count": len(values)}
        for name, values in sorted(subsystem_paths.items(), key=lambda item: (-len(item[1]), item[0]))[:8]
    ]

    entrypoints = []
    for sample in source_samples:
        reason = _entrypoint_reason(sample.path)
        if reason:
            entrypoints.append({"path": sample.path, "reason": reason})
    entrypoints = entrypoints[:8]

    imports_by_path = {sample.path: _extract_imports(sample.content) for sample in source_samples}
    dependencies = [str(item) for item in profile.get("dependencies", []) if str(item).strip()]
    dependency_usage: list[dict[str, Any]] = []
    for dependency in dependencies:
        references: list[dict[str, Any]] = []
        files: set[str] = set()
        occurrences = 0
        for path, imports in imports_by_path.items():
            for imported, line_number, excerpt in imports:
                if not _dependency_matches_import(dependency, imported):
                    continue
                occurrences += 1
                files.add(path)
                if len(references) < _MAX_REFERENCES_PER_DEPENDENCY:
                    references.append(
                        {
                            "path": path,
                            "line": line_number,
                            "kind": "import_reference",
                            "imported": imported,
                            "excerpt": excerpt,
                        }
                    )
        if references:
            dependency_usage.append(
                {
                    "dependency": dependency,
                    "files": sorted(files)[:8],
                    "occurrences_in_sample": occurrences,
                    "references": references,
                }
            )
        if len(dependency_usage) >= _MAX_DEPENDENCIES:
            break

    sampled_paths = {sample.path for sample in source_samples}
    edges: list[dict[str, str]] = []
    seen_edges: set[tuple[str, str]] = set()
    for source_path, imports in imports_by_path.items():
        for imported, _line_number, _excerpt in imports:
            target = _relative_target(source_path, imported, sampled_paths)
            if not target:
                continue
            pair = (source_path, target)
            if pair in seen_edges:
                continue
            seen_edges.add(pair)
            edges.append({"from": source_path, "to": target, "kind": "static_import"})
            if len(edges) >= _MAX_EDGES:
                break
        if len(edges) >= _MAX_EDGES:
            break

    coverage = "source_sampled" if source_samples else "manifest_paths_only"
    return {
        "version": _ARCHITECTURE_VERSION,
        "coverage": coverage,
        "source_files_sampled": len(source_samples),
        "source_file_cap": _MAX_SOURCE_FILES,
        "subsystems": subsystems,
        "entrypoints": entrypoints,
        "dependency_usage": dependency_usage,
        "static_edges": edges,
        "evidence_paths": evidence_paths[:_MAX_SOURCE_FILES],
        "limitations": [
            "只读取有界源码样本和静态 import；不执行项目代码。",
            "静态引用不等于运行时可达，未采样文件中的使用点可能未显示。",
        ],
    }


def _identity_terms(value: str) -> set[str]:
    generic = {"sdk", "python", "typescript", "javascript", "js", "py", "repo", "repository"}
    return {
        token
        for token in re.findall(r"[a-z0-9]+", value.casefold())
        if len(token) >= 4 and token not in generic
    }


def architecture_usage_references(profile: dict[str, Any], entity: str) -> list[dict[str, Any]]:
    """Return persisted source references related to an external entity/dependency."""
    snapshot = profile.get("architecture_snapshot", {})
    if not isinstance(snapshot, dict):
        return []
    tokens = {
        token.casefold().replace("_", "-")
        for token in re.findall(r"[A-Za-z][A-Za-z0-9_@./-]+", entity)
        if len(token) >= 2
    }
    entity_terms = _identity_terms(entity)
    refs: list[dict[str, Any]] = []
    for item in snapshot.get("dependency_usage", []) or []:
        if not isinstance(item, dict):
            continue
        dependency = str(item.get("dependency") or "")
        dep_norm = dependency.casefold().replace("_", "-")
        direct_match = any(
            dep_norm in token or token.endswith("/" + dep_norm) or token == dep_norm
            for token in tokens
        )
        if not direct_match and not (_identity_terms(dependency) & entity_terms):
            continue
        for ref in item.get("references", []) or []:
            if not isinstance(ref, dict):
                continue
            refs.append(
                {
                    "reference_id": f"arch-usage-{len(refs) + 1}",
                    "path": str(ref.get("path") or ""),
                    "line": int(ref.get("line") or 0),
                    "excerpt": str(ref.get("excerpt") or "")[:260],
                    "kind": "architecture_import_reference",
                }
            )
            if len(refs) >= 12:
                return refs
    return refs
