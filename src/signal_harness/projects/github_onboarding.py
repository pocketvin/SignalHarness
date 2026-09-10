"""Safe GitHub URL onboarding without executing repository code."""

from __future__ import annotations

import base64
import binascii
import re
from typing import Any
from urllib.parse import quote, urlparse

import httpx

from signal_harness.projects.onboarding import ProjectDraft, ProjectManifest, draft_project
from signal_harness.tools.github_signal import github_api_headers

_MAX_MANIFEST_BYTES = 1_000_000
_MAX_MANIFESTS = 20
_MAX_PATHS = 500
_ALLOWED_MANIFEST_NAMES = {
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


def github_repository_from_url(value: str) -> str:
    """Return owner/repo from a GitHub repository root URL."""

    raw = value.strip()
    if not raw:
        raise ValueError("GitHub repository URL is required")
    parsed = urlparse(raw)
    if parsed.scheme not in {"http", "https"} or parsed.hostname not in {"github.com", "www.github.com"}:
        raise ValueError("URL must be a github.com repository URL")
    parts = [part for part in parsed.path.split("/") if part]
    if len(parts) != 2:
        raise ValueError("Use the repository root URL, for example https://github.com/owner/repo")
    owner, name = parts
    if name.endswith(".git"):
        name = name[:-4]
    if not owner or not name or not re.fullmatch(r"[A-Za-z0-9_.-]+", owner) or not re.fullmatch(
        r"[A-Za-z0-9_.-]+", name
    ):
        raise ValueError("Invalid GitHub repository URL")
    return f"{owner}/{name}"


async def draft_github_project(
    url: str,
    *,
    client: httpx.AsyncClient | None = None,
) -> ProjectDraft:
    """Build an onboarding draft from GitHub metadata, tree paths, and safe manifests."""

    repo = github_repository_from_url(url)
    owns_client = client is None
    http = client or httpx.AsyncClient(timeout=20, follow_redirects=True)
    try:
        metadata = await _get_json(http, f"https://api.github.com/repos/{repo}")
        canonical_repo = str(metadata.get("full_name") or repo)
        repo_name = str(metadata.get("name") or canonical_repo.rsplit("/", 1)[-1])
        default_branch = str(metadata.get("default_branch") or "main")
        tree = await _get_json(
            http,
            f"https://api.github.com/repos/{canonical_repo}/git/trees/{quote(default_branch, safe='')}",
            params={"recursive": "1"},
        )
        tree_payload = tree.get("tree")
        tree_rows: list[Any] = tree_payload if isinstance(tree_payload, list) else []
        paths = [
            str(item.get("path"))
            for item in tree_rows
            if isinstance(item, dict) and item.get("type") == "blob" and item.get("path")
        ][:_MAX_PATHS]
        manifests: list[ProjectManifest] = []
        manifest_rows = [
            item
            for item in tree_rows
            if isinstance(item, dict)
            and item.get("type") == "blob"
            and _is_manifest_path(str(item.get("path") or ""))
        ]
        root_manifest_rows = [
            item
            for item in manifest_rows
            if "/" not in str(item.get("path") or "").strip("/")
        ]
        if root_manifest_rows:
            # A repository-root manifest defines the primary project. Nested example/demo
            # manifests remain visible in the bounded path evidence but must not overwrite
            # the root project's name, purpose, dependencies, or lockfile evidence.
            manifest_rows = root_manifest_rows
        else:
            # Monorepos without a root manifest: prefer the shallowest package layer
            # instead of whichever nested example happens to appear first in the tree.
            min_depth = min(
                (str(item.get("path") or "").count("/") for item in manifest_rows),
                default=0,
            )
            manifest_rows = [
                item
                for item in manifest_rows
                if str(item.get("path") or "").count("/") == min_depth
            ]
        manifest_rows.sort(key=lambda item: str(item.get("path") or "").lower())
        for item in manifest_rows[:_MAX_MANIFESTS]:
            path = str(item.get("path") or "")
            size = int(item.get("size") or 0)
            sha = str(item.get("sha") or "")
            if not sha or size > _MAX_MANIFEST_BYTES:
                continue
            blob = await _get_json(
                http,
                f"https://api.github.com/repos/{canonical_repo}/git/blobs/{sha}",
            )
            if str(blob.get("encoding") or "") != "base64":
                continue
            raw = str(blob.get("content") or "").replace("\n", "")
            try:
                decoded = base64.b64decode(raw, validate=True)
            except (ValueError, binascii.Error):
                continue
            if len(decoded) > _MAX_MANIFEST_BYTES:
                continue
            manifests.append(
                ProjectManifest(path=path, content=decoded.decode("utf-8", errors="replace"))
            )
    finally:
        if owns_client:
            await http.aclose()

    draft = draft_project(manifests=manifests, paths=paths, name_hint=repo_name)
    project_id = _github_project_id(canonical_repo)
    profile = dict(draft.project_profile)
    description = str(metadata.get("description") or "").strip()
    if description and (
        not manifests or "purpose not declared in a supported manifest" in str(profile.get("purpose") or "")
    ):
        profile["purpose"] = description
        profile["goal"] = f"Monitor external changes that can affect {repo_name}: {description}"
    profile["project_name"] = repo_name
    profile["repository"] = {
        "provider": "github",
        "repo": canonical_repo,
        "url": str(metadata.get("html_url") or f"https://github.com/{canonical_repo}"),
        "default_branch": default_branch,
        "private": bool(metadata.get("private", False)),
    }
    evidence = dict(profile.get("evidence") or {})
    evidence["github_repository"] = canonical_repo
    evidence["github_tree_truncated"] = bool(tree.get("truncated", False))
    profile["evidence"] = evidence

    watchlist = dict(draft.watchlist)
    github = dict(watchlist.get("github") or {})
    repositories = [dict(item) for item in github.get("repositories", []) if isinstance(item, dict)]
    existing = next(
        (
            item
            for item in repositories
            if str(item.get("repo") or "").lower() == canonical_repo.lower()
        ),
        None,
    )
    if existing is None:
        repositories.insert(
            0,
            {
                "repo": canonical_repo,
                "project_owned": True,
                "events": ["commits", "pull_requests", "releases"],
            },
        )
    else:
        existing["project_owned"] = True
        existing["events"] = list(
            dict.fromkeys([*existing.get("events", []), "commits", "pull_requests", "releases"])
        )
    github["repositories"] = repositories
    watchlist["github"] = github

    return draft.model_copy(
        update={
            "id": project_id,
            "name": repo_name,
            "description": (
                f"GitHub project {canonical_repo}; detected {len(manifests)} safe manifests "
                f"and {len(paths)} repository paths."
            ),
            "project_profile": profile,
            "watchlist": watchlist,
            "evidence_files": [item.path for item in manifests],
            "detected_paths": paths,
        }
    )


async def _get_json(
    client: httpx.AsyncClient,
    url: str,
    *,
    params: dict[str, str] | None = None,
) -> dict[str, Any]:
    try:
        response = await client.get(url, params=params, headers=github_api_headers())
        response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code == 404:
            raise ValueError("GitHub repository or repository data was not found") from exc
        if exc.response.status_code in {401, 403}:
            raise ValueError("GitHub access was denied or rate limited") from exc
        raise ValueError(f"GitHub request failed with HTTP {exc.response.status_code}") from exc
    except httpx.HTTPError as exc:
        raise ValueError("GitHub request failed") from exc
    payload = response.json()
    if not isinstance(payload, dict):
        raise ValueError("GitHub returned an unexpected response")
    return payload


def _is_manifest_path(path: str) -> bool:
    base = path.rsplit("/", 1)[-1].lower()
    return base in _ALLOWED_MANIFEST_NAMES or (base.startswith("requirements") and base.endswith(".txt"))


def _github_project_id(repo: str) -> str:
    slug = re.sub(r"[^a-z0-9_-]+", "-", repo.lower().replace("/", "-"))
    return slug.strip("-")[:64] or "github-project"
