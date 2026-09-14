"""GitHub release/issue/commit collection plus identity-complete merged-PR GraphQL reads."""

from __future__ import annotations

import json
import os
import subprocess
from functools import lru_cache
from datetime import datetime
from typing import Any, Literal

import httpx
from pydantic import BaseModel, Field, model_validator

from signal_harness.runtime.tools_base import BaseTool, ToolExecutionContext, ToolResult
from signal_harness.signal.normalizer import normalize_github_event

HTTP_AUTH_HEADER = "Authori" + "zation"
BEARER_PREFIX = "Bear" + "er"
GITHUB_API_VERSION = "2026-03-10"
MAX_GITHUB_PAGES = 20
GITHUB_GRAPHQL_URL = "https://api.github.com/graphql"

_MERGED_PULLS_QUERY = """
query($owner: String!, $name: String!, $after: String) {
  repository(owner: $owner, name: $name) {
    pullRequests(
      first: 100
      after: $after
      states: MERGED
      orderBy: {field: UPDATED_AT, direction: DESC}
    ) {
      nodes {
        id
        number
        title
        body
        url
        createdAt
        updatedAt
        mergedAt
        mergeCommit { oid }
        author { login }
        authorAssociation
        mergedBy { login }
        baseRefName
        headRefName
        labels(first: 50) { nodes { name } }
      }
      pageInfo { hasNextPage endCursor }
    }
  }
}
"""

GitHubAction = Literal[
    "fetch_repo_releases",
    "fetch_repo_issues",
    "fetch_repo_commits",
    "fetch_repo_merged_pulls",
    "search_repositories",
    "normalize_github_event",
]
GitHubEventKind = Literal[
    "github_release",
    "github_issue",
    "github_commit",
    "github_pull_request",
    "github_repository",
]


class GitHubSignalInput(BaseModel):
    action: GitHubAction
    repo: str = ""
    since: datetime | None = None
    raw: dict[str, Any] | None = None
    event_kind: GitHubEventKind | None = None
    query: str = ""
    max_results: int = Field(default=5, ge=1, le=10)

    @model_validator(mode="after")
    def _validate_action_inputs(self) -> GitHubSignalInput:
        if self.action.startswith("fetch_") and "/" not in self.repo:
            raise ValueError("repo must use owner/name format")
        if self.action == "normalize_github_event" and self.raw is None:
            raise ValueError("raw is required for normalization")
        if self.action == "search_repositories" and not self.query.strip():
            raise ValueError("query is required for repository discovery")
        return self


class GitHubSignalTool(BaseTool):
    """Read GitHub project signals and normalize individual payloads."""

    name = "github_signal"
    description = "Fetch GitHub releases/issues/commits/merged PRs, discover repositories, or normalize one payload."
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

        headers = _github_headers()
        try:
            if arguments.action == "fetch_repo_merged_pulls":
                payload, metadata = await _fetch_merged_pulls_graphql(
                    repo=arguments.repo,
                    since=arguments.since,
                    headers=headers,
                )
            elif arguments.action == "search_repositories":
                payload, metadata = await _search_repositories(
                    query=arguments.query,
                    since=arguments.since,
                    max_results=arguments.max_results,
                    headers=headers,
                )
            else:
                endpoint, params, cutoff_fields = _request_plan(arguments)
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
        elif arguments.action == "search_repositories":
            payload = [_enrich_discovered_repository(item, arguments.query) for item in payload]

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


def _pull_graphql_row(item: dict[str, Any]) -> dict[str, Any]:
    labels = _as_dict(item.get("labels")).get("nodes")
    label_rows = labels if isinstance(labels, list) else []
    author = _as_dict(item.get("author"))
    merged_by = _as_dict(item.get("mergedBy"))
    merge_commit = _as_dict(item.get("mergeCommit"))
    return {
        "id": item.get("id"),
        "node_id": item.get("id"),
        "number": item.get("number"),
        "title": item.get("title"),
        "body": item.get("body"),
        "html_url": item.get("url"),
        "created_at": item.get("createdAt"),
        "updated_at": item.get("updatedAt"),
        "merged_at": item.get("mergedAt"),
        "merge_commit_sha": merge_commit.get("oid"),
        "author_association": item.get("authorAssociation"),
        "user": {"login": author.get("login")},
        "merged_by": {"login": merged_by.get("login")},
        "base": {"ref": item.get("baseRefName")},
        "head": {"ref": item.get("headRefName")},
        "labels": [
            {"name": label.get("name")}
            for label in label_rows
            if isinstance(label, dict) and label.get("name")
        ],
    }


async def _fetch_merged_pulls_graphql(
    *,
    repo: str,
    since: datetime | None,
    headers: dict[str, str],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Fetch merged PRs with mergeCommit oid so PR and commit share one canonical Change.

    GitHub's REST pull-list response does not reliably carry ``merge_commit_sha``. If that
    identity is missing, ``git_change_identity`` cannot collapse a merged PR with the matching
    commit and high-velocity project activity nearly doubles. GraphQL returns ``mergeCommit.oid``
    in the paginated list, preserving source-owned identity without one detail request per PR.
    """

    owner, name = repo.split("/", 1)
    payload: list[dict[str, Any]] = []
    after: str | None = None
    pages = 0
    history_limited = False
    diagnostics: list[str] = []

    async with httpx.AsyncClient(timeout=20, follow_redirects=True) as client:
        while pages < MAX_GITHUB_PAGES:
            response = await client.post(
                GITHUB_GRAPHQL_URL,
                headers=headers,
                json={
                    "query": _MERGED_PULLS_QUERY,
                    "variables": {"owner": owner, "name": name, "after": after},
                },
            )
            response.raise_for_status()
            body = response.json()
            if not isinstance(body, dict) or body.get("errors"):
                raise ValueError("GitHub GraphQL returned an invalid merged-pull payload")
            repository = _as_dict(_as_dict(body.get("data")).get("repository"))
            pulls = _as_dict(repository.get("pullRequests"))
            nodes = pulls.get("nodes")
            page_info = _as_dict(pulls.get("pageInfo"))
            if not isinstance(nodes, list) or not all(isinstance(item, dict) for item in nodes):
                raise ValueError("GitHub GraphQL returned invalid pull request nodes")

            mapped = [_pull_graphql_row(item) for item in nodes]
            payload.extend(
                item
                for item in mapped
                if item.get("merged_at")
                and (since is None or _is_at_or_after(item.get("merged_at"), since))
            )
            pages += 1

            # pullRequests is ordered by updatedAt DESC. Since mergedAt <= updatedAt, once
            # the oldest updated row is before the lower bound no later page can contain an
            # in-window merge.
            if since is not None and mapped:
                oldest_updated = mapped[-1].get("updated_at")
                if oldest_updated and not _is_at_or_after(oldest_updated, since):
                    break

            if not bool(page_info.get("hasNextPage")):
                break
            cursor = page_info.get("endCursor")
            if not isinstance(cursor, str) or not cursor:
                raise ValueError("GitHub GraphQL pagination cursor missing")
            after = cursor
        else:
            history_limited = True

    missing_identity = sum(not str(item.get("merge_commit_sha") or "").strip() for item in payload)
    if missing_identity:
        diagnostics.append(f"{missing_identity} merged pull(s) missing mergeCommit oid")
    if history_limited:
        diagnostics.append(f"pagination capped at {MAX_GITHUB_PAGES} pages")
    return payload, {
        "pages_fetched": pages,
        "coverage_status": "partial" if history_limited else "complete",
        "history_limited": history_limited,
        "diagnostics": diagnostics,
        "identity_source": "graphql_merge_commit_oid",
    }


async def _search_repositories(
    *,
    query: str,
    since: datetime | None,
    max_results: int,
    headers: dict[str, str],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Discover previously-unknown repositories for one project-conditioned query."""

    qualified = query.strip()
    if since is not None:
        qualified += f" created:>={since.date().isoformat()}"
    params: dict[str, str | int] = {
        "q": qualified,
        "sort": "stars",
        "order": "desc",
        "per_page": max_results,
    }
    async with httpx.AsyncClient(timeout=20, follow_redirects=True) as client:
        response = await client.get(
            "https://api.github.com/search/repositories",
            params=params,
            headers=headers,
        )
        response.raise_for_status()
        body = response.json()
    if not isinstance(body, dict) or not isinstance(body.get("items"), list):
        raise ValueError("GitHub repository search returned an invalid payload")
    items = [
        item
        for item in body["items"]
        if isinstance(item, dict)
        and not bool(item.get("fork", False))
        and not bool(item.get("archived", False))
    ][:max_results]
    return items, {
        "pages_fetched": 1,
        "coverage_status": "unknown",
        "history_limited": False,
        "diagnostics": (
            ["GitHub search reported incomplete results"]
            if body.get("incomplete_results")
            else []
        ),
        "search_total_count": int(body.get("total_count") or 0),
        "discovery_query": query,
    }


def _enrich_discovered_repository(item: dict[str, Any], query: str) -> dict[str, Any]:
    return {
        "id": item.get("id"),
        "node_id": item.get("node_id"),
        "name": item.get("name"),
        "full_name": item.get("full_name"),
        "description": item.get("description") or "",
        "html_url": item.get("html_url") or "",
        "homepage": item.get("homepage") or "",
        "created_at": item.get("created_at"),
        "updated_at": item.get("updated_at"),
        "pushed_at": item.get("pushed_at"),
        "language": item.get("language") or "",
        "topics": item.get("topics") if isinstance(item.get("topics"), list) else [],
        "owner_login": _as_dict(item.get("owner")).get("login"),
        "discovery_stars": int(item.get("stargazers_count") or 0),
        "discovery_origin": "discovered",
        "discovery_basis": query,
        "source_authority": "maintainer",
        "official": False,
    }


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
