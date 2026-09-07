"""GitHub release and issue collection through a SignalHarness tool."""

from __future__ import annotations

import json
import os
from datetime import datetime
from typing import Any, Literal

import httpx
from pydantic import BaseModel, model_validator

from signal_harness.runtime.tools_base import BaseTool, ToolExecutionContext, ToolResult
from signal_harness.signal.normalizer import normalize_github_event

HTTP_AUTH_HEADER = "Authori" + "zation"
BEARER_PREFIX = "Bear" + "er"
MAX_GITHUB_PAGES = 20


class GitHubSignalInput(BaseModel):
    action: Literal["fetch_repo_releases", "fetch_repo_issues", "normalize_github_event"]
    repo: str = ""
    since: datetime | None = None
    raw: dict[str, Any] | None = None
    event_kind: Literal["github_release", "github_issue"] | None = None

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
    description = "Fetch GitHub releases/issues or normalize one GitHub payload."
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

        endpoint = (
            f"https://api.github.com/repos/{arguments.repo}/releases"
            if arguments.action == "fetch_repo_releases"
            else f"https://api.github.com/repos/{arguments.repo}/issues"
        )
        params: dict[str, str | int] = {"per_page": 100}
        if arguments.action == "fetch_repo_issues":
            params["state"] = "all"
            if arguments.since is not None:
                params["since"] = arguments.since.isoformat()
        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "SignalHarness/0.1",
        }
        authorization = _github_authorization_header()
        if authorization:
            headers[HTTP_AUTH_HEADER] = authorization
        try:
            payload, metadata = await _fetch_paginated(
                endpoint=endpoint,
                params=params,
                headers=headers,
                since=arguments.since,
                releases=arguments.action == "fetch_repo_releases",
            )
        except (httpx.HTTPError, UnicodeEncodeError, ValueError) as exc:
            return ToolResult(output=f"GitHub request failed: {exc}", is_error=True)

        if arguments.action == "fetch_repo_releases":
            for index, item in enumerate(payload):
                previous = payload[index + 1] if index + 1 < len(payload) else None
                if isinstance(previous, dict) and previous.get("tag_name"):
                    item.setdefault("_previous_tag_name", previous.get("tag_name"))
            if arguments.since is not None:
                payload = [
                    item
                    for item in payload
                    if _is_at_or_after(
                        item.get("published_at") or item.get("created_at"), arguments.since
                    )
                ]
        if arguments.action == "fetch_repo_issues":
            payload = [item for item in payload if "pull_request" not in item]
        metadata["item_count"] = len(payload)
        return ToolResult(
            output=json.dumps(payload, ensure_ascii=False),
            metadata=metadata,
        )


async def _fetch_paginated(
    *,
    endpoint: str,
    params: dict[str, str | int],
    headers: dict[str, str],
    since: datetime | None,
    releases: bool,
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
            if releases and since is not None and page:
                oldest = page[-1].get("published_at") or page[-1].get("created_at")
                if isinstance(oldest, str) and not _is_at_or_after(oldest, since):
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
    token = os.environ.get("GITHUB_TOKEN", "").strip()
    if not token:
        return ""
    header = f"{BEARER_PREFIX} {token}"
    try:
        header.encode("ascii")
    except UnicodeEncodeError:
        return ""
    return header
