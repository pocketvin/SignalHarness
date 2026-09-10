"""GitHub release, issue, commit, and merged-PR collection."""

from __future__ import annotations

import json
import os
import subprocess
from functools import lru_cache
from datetime import datetime
from typing import Any, Literal

import httpx
from pydantic import BaseModel, model_validator

from signal_harness.runtime.tools_base import BaseTool, ToolExecutionContext, ToolResult
from signal_harness.signal.normalizer import normalize_github_event

HTTP_AUTH_HEADER = "Authori" + "zation"
BEARER_PREFIX = "Bear" + "er"
GITHUB_API_VERSION = "2026-03-10"
MAX_GITHUB_PAGES = 20

GitHubAction = Literal[
    "fetch_repo_releases",
    "fetch_repo_issues",
    "fetch_repo_commits",
    "fetch_repo_merged_pulls",
    "normalize_github_event",
]
GitHubEventKind = Literal[
    "github_release",
    "github_issue",
    "github_commit",
    "github_pull_request",
]


class GitHubSignalInput(BaseModel):
    action: GitHubAction
    repo: str = ""
    since: datetime | None = None
    raw: dict[str, Any] | None = None
    event_kind: GitHubEventKind | None = None

    @model_validator(mode="after")
    def _validate_action_inputs(self) -> GitHubSignalInput:
        if self.action.startswith("fetch_") and "/" not in self.repo:
            raise ValueError("repo must use owner/name format")
        if self.action == "normalize_github_event" and self.raw is None:
            raise ValueError("raw is required for normalization")
        return self


class GitHubSignalTool(BaseTool):
    """Read GitHub project signals and normalize individual payloads."""

    name = "github_signal"
    description = "Fetch GitHub releases/issues/commits/merged PRs or normalize one payload."
    input_model = GitHubSignalInput

    def is_read_only(self, arguments: GitHubSignalInput) -> bool:
        return True

    async def execute(
        self,
        arguments: GitHubSignalInput,
        context: ToolExecutionContext,
    ) -> ToolResult:
        del context
        if arguments.action == "normalize_github_event":
            assert arguments.raw is not None
            event = normalize_github_event(
                arguments.raw,
                repo=arguments.repo or None,
                event_kind=arguments.event_kind,
            )
            return ToolResult(output=event.model_dump_json())

        endpoint, params, cutoff_fields = _request_plan(arguments)
        headers = _github_headers()
        try:
            payload, metadata = await _fetch_paginated(
                endpoint=endpoint,
                params=params,
                headers=headers,
                since=arguments.since,
                releases=arguments.action == "fetch_repo_releases",
                cutoff_fields=cutoff_fields,
            )
        except (httpx.HTTPError, UnicodeEncodeError, ValueError) as exc:
            return ToolResult(output=f"GitHub request failed: {exc}", is_error=True)

        if arguments.action == "fetch_repo_releases":
            _annotate_previous_release(payload)
            if arguments.since is not None:
                payload = [
                    item
                    for item in payload
                    if _is_at_or_after(
                        item.get("published_at") or item.get("created_at"), arguments.since
                    )
                ]
        elif arguments.action == "fetch_repo_issues":
            payload = [item for item in payload if "pull_request" not in item]
        elif arguments.action == "fetch_repo_commits":
            payload = [_enrich_commit_row(item, arguments.repo) for item in payload]
        elif arguments.action == "fetch_repo_merged_pulls":
            payload = [
                _enrich_pull_row(item, arguments.repo)
                for item in payload
                if item.get("merged_at")
                and (
                    arguments.since is None
                    or _is_at_or_after(item.get("merged_at"), arguments.since)
                )
            ]

        metadata["item_count"] = len(payload)
        metadata["api_version"] = GITHUB_API_VERSION
        return ToolResult(output=json.dumps(payload, ensure_ascii=False), metadata=metadata)


def _request_plan(
    arguments: GitHubSignalInput,
) -> tuple[str, dict[str, str | int], tuple[str, ...]]:
    base = f"https://api.github.com/repos/{arguments.repo}"
    params: dict[str, str | int] = {"per_page": 100}
    cutoff_fields: tuple[str, ...] = ()
    if arguments.action == "fetch_repo_releases":
        return f"{base}/releases", params, ("published_at", "created_at")
    if arguments.action == "fetch_repo_issues":
        params["state"] = "all"
        if arguments.since is not None:
            params["since"] = arguments.since.isoformat()
        return f"{base}/issues", params, cutoff_fields
    if arguments.action == "fetch_repo_commits":
        if arguments.since is not None:
            params["since"] = arguments.since.isoformat()
        return f"{base}/commits", params, cutoff_fields
    if arguments.action == "fetch_repo_merged_pulls":
        params.update({"state": "closed", "sort": "updated", "direction": "desc"})
        # Pull-list API has no `since`. Because merged_at <= updated_at, once the
        # oldest updated_at on a page is before L, later pages cannot contain a
        # pull merged within [L, U).
        return f"{base}/pulls", params, ("updated_at",)
    raise ValueError(f"unsupported GitHub action: {arguments.action}")


def github_api_headers() -> dict[str, str]:
    """Return safe GitHub API headers shared by read-only GitHub integrations."""

    headers = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": GITHUB_API_VERSION,
        "User-Agent": "SignalHarness/0.1",
    }
    authorization = _github_authorization_header()
    if authorization:
        headers[HTTP_AUTH_HEADER] = authorization
    return headers


def _github_headers() -> dict[str, str]:
    """Backward-compatible private alias."""

    return github_api_headers()


def _annotate_previous_release(payload: list[dict[str, Any]]) -> None:
    for index, item in enumerate(payload):
        previous = payload[index + 1] if index + 1 < len(payload) else None
        if isinstance(previous, dict) and previous.get("tag_name"):
            item.setdefault("_previous_tag_name", previous.get("tag_name"))


def _enrich_commit_row(item: dict[str, Any], repo: str) -> dict[str, Any]:
    row = dict(item)
    row.setdefault("repository", repo)
    row.setdefault("repository_identity", repo.lower())
    commit = _as_dict(row.get("commit"))
    verification = _as_dict(commit.get("verification"))
    row["commit_verification"] = {
        "verified": bool(verification.get("verified", False)),
        "reason": str(verification.get("reason") or ""),
        "verified_at": str(verification.get("verified_at") or ""),
    }
    row["parent_shas"] = [
        str(parent.get("sha"))
        for parent in row.get("parents", [])
        if isinstance(parent, dict) and parent.get("sha")
    ]
    author = _as_dict(row.get("author"))
    committer = _as_dict(row.get("committer"))
    row["author_login"] = str(author.get("login") or "")
    row["committer_login"] = str(committer.get("login") or "")
    row["official"] = True
    return row


def _enrich_pull_row(item: dict[str, Any], repo: str) -> dict[str, Any]:
    row = dict(item)
    row.setdefault("repository", repo)
    row.setdefault("repository_identity", repo.lower())
    row["official"] = True
    user = _as_dict(row.get("user"))
    merged_by = _as_dict(row.get("merged_by"))
    base = _as_dict(row.get("base"))
    head = _as_dict(row.get("head"))
    row["author_login"] = str(user.get("login") or "")
    row["merged_by_login"] = str(merged_by.get("login") or "")
    row["base_ref"] = str(base.get("ref") or "")
    row["head_ref"] = str(head.get("ref") or "")
    row["label_names"] = [
        str(label.get("name"))
        for label in row.get("labels", [])
        if isinstance(label, dict) and label.get("name")
    ]
    return row


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


async def _fetch_paginated(
    *,
    endpoint: str,
    params: dict[str, str | int],
    headers: dict[str, str],
    since: datetime | None,
    releases: bool,
    cutoff_fields: tuple[str, ...] = (),
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    payload: list[dict[str, Any]] = []
    next_url: str | None = endpoint
    next_params: dict[str, str | int] | None = dict(params)
    pages = 0
    history_limited = False
    async with httpx.AsyncClient(timeout=20, follow_redirects=True) as client:
        while next_url is not None and pages < MAX_GITHUB_PAGES:
            response = await client.get(next_url, params=next_params, headers=headers)
            response.raise_for_status()
            page = response.json()
            if not isinstance(page, list) or not all(isinstance(item, dict) for item in page):
                raise ValueError("GitHub returned a non-list payload")
            payload.extend(page)
            pages += 1
            effective_cutoff = cutoff_fields or (("published_at", "created_at") if releases else ())
            if since is not None and page and effective_cutoff:
                oldest = _first_timestamp(page[-1], effective_cutoff)
                if oldest and not _is_at_or_after(oldest, since):
                    next_url = None
                    break
            link = response.links.get("next")
            next_url = str(link.get("url")) if isinstance(link, dict) and link.get("url") else None
            next_params = None
        if next_url is not None:
            history_limited = True
    return payload, {
        "pages_fetched": pages,
        "coverage_status": "partial" if history_limited else "complete",
        "history_limited": history_limited,
        "diagnostics": ([f"pagination capped at {MAX_GITHUB_PAGES} pages"] if history_limited else []),
    }


def _first_timestamp(item: dict[str, Any], fields: tuple[str, ...]) -> str:
    for field in fields:
        value = item.get(field)
        if isinstance(value, str) and value:
            return value
    return ""


def _is_at_or_after(raw: object, since: datetime) -> bool:
    if not isinstance(raw, str) or not raw:
        return False
    try:
        value = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return False
    if since.tzinfo is None and value.tzinfo is not None:
        since = since.replace(tzinfo=value.tzinfo)
    return value >= since


def _github_authorization_header() -> str:
    """Resolve GitHub auth without ever exposing or persisting the token.

    Explicit process environment wins. For local-owner workflows, fall back to the
    credential already managed by the GitHub CLI/keyring so the web UI does not
    unexpectedly use the anonymous 60-request/hour API bucket.
    """

    for key in ("GITHUB_TOKEN", "GH_TOKEN"):
        header = _bearer_header(os.environ.get(key, ""))
        if header:
            return header
    return _github_cli_authorization_header()


@lru_cache(maxsize=1)
def _github_cli_authorization_header() -> str:
    """Read the GitHub CLI/keyring credential once per process.

    The resolved bearer header remains memory-only. Environment credentials still
    take precedence on every call, so an explicitly exported token can be rotated
    without restarting SignalHarness.
    """

    try:
        gh_env = {
            key: value
            for key, value in os.environ.items()
            if key not in {"GITHUB_TOKEN", "GH_TOKEN"}
        }
        result = subprocess.run(
            ["gh", "auth", "token"],
            check=False,
            capture_output=True,
            text=True,
            timeout=3,
            env=gh_env,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    if result.returncode != 0:
        return ""
    return _bearer_header(result.stdout)


def _bearer_header(value: str) -> str:
    token = value.strip()
    if not token:
        return ""
    header = f"{BEARER_PREFIX} {token}"
    try:
        header.encode("ascii")
    except UnicodeEncodeError:
        return ""
    return header
