"""Read-only local Git collection for connected project repositories."""

from __future__ import annotations

import asyncio
import json
import re
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, field_validator

from signal_harness.runtime.tools_base import BaseTool, ToolExecutionContext, ToolResult
from signal_harness.signal.source_identity import (
    github_repository_from_remote,
    normalize_repository_identity,
)

MAX_LOCAL_GIT_COMMITS = 500
_RECORD_SEPARATOR = "\x1e"
_FIELD_SEPARATOR = "\x1f"


class LocalGitInput(BaseModel):
    action: Literal["fetch_commits"] = "fetch_commits"
    repo_path: str
    since: datetime | None = None
    max_commits: int = Field(default=MAX_LOCAL_GIT_COMMITS, ge=1, le=2000)

    @field_validator("repo_path")
    @classmethod
    def _non_blank_path(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("repo_path must not be blank")
        return value


class LocalGitTool(BaseTool):
    """Collect bounded commit metadata without executing project code."""

    name = "local_git"
    description = "Read bounded commit metadata from an explicitly connected local Git repository."
    input_model = LocalGitInput

    def is_read_only(self, arguments: LocalGitInput) -> bool:
        return True

    async def execute(
        self,
        arguments: LocalGitInput,
        context: ToolExecutionContext,
    ) -> ToolResult:
        del context
        try:
            rows, metadata = await asyncio.to_thread(_collect_commits, arguments)
        except (OSError, subprocess.SubprocessError, ValueError) as exc:
            return ToolResult(output=f"Local Git request failed: {exc}", is_error=True)
        return ToolResult(
            output=json.dumps(rows, ensure_ascii=False),
            metadata=metadata,
        )


def _collect_commits(arguments: LocalGitInput) -> tuple[list[dict[str, object]], dict[str, object]]:
    requested = Path(arguments.repo_path).expanduser().resolve()
    if not requested.is_dir():
        raise ValueError("repo_path must be an existing directory")
    root = Path(_git(requested, "rev-parse", "--show-toplevel").strip()).resolve()
    remote = _git_optional(root, "config", "--get", "remote.origin.url").strip()
    github_repo = github_repository_from_remote(remote) if remote else None
    repository_identity = github_repo or normalize_repository_identity(str(root))

    command = [
        "log",
        f"--max-count={arguments.max_commits + 1}",
        "--date=iso-strict",
        "--format=%H%x1f%P%x1f%aI%x1f%cI%x1f%an%x1f%s%x1f%B%x1e",
    ]
    if arguments.since is not None:
        command.append(f"--since={arguments.since.isoformat()}")
    raw_log = _git(root, *command)
    commits = [_parse_commit(record, root, repository_identity, github_repo) for record in raw_log.split(_RECORD_SEPARATOR) if record.strip()]
    history_limited = len(commits) > arguments.max_commits
    commits = commits[: arguments.max_commits]
    diagnostics: list[str] = []
    if history_limited:
        diagnostics.append(f"local Git history capped at {arguments.max_commits} commits")
    if not remote:
        diagnostics.append("origin remote unavailable; local repository identity used")
    return commits, {
        "coverage_status": "partial" if history_limited else "complete",
        "history_limited": history_limited,
        "pages_fetched": 1,
        "item_count": len(commits),
        "repository_root": str(root),
        "repository_identity": repository_identity,
        "diagnostics": diagnostics,
    }


def _parse_commit(
    record: str,
    root: Path,
    repository_identity: str,
    github_repo: str | None,
) -> dict[str, object]:
    fields = record.lstrip("\n").split(_FIELD_SEPARATOR, 6)
    if len(fields) != 7:
        raise ValueError("git log returned an unexpected record shape")
    sha, parent_text, authored_at, committed_at, author_name, title, message = fields
    parents = [value for value in parent_text.split() if value]
    pr_number = _pull_request_number(title, message)
    html_url = f"https://github.com/{github_repo}/commit/{sha}" if github_repo else ""
    return {
        "id": sha,
        "sha": sha,
        "repository_identity": repository_identity,
        "repository_path": str(root),
        "github_repository": github_repo,
        "title": title.strip() or f"Commit {sha[:12]}",
        "message": message.strip(),
        "author_name": author_name.strip(),
        "authored_at": authored_at.strip(),
        "committed_at": committed_at.strip(),
        "parents": parents,
        "is_merge_commit": len(parents) > 1,
        "pull_request_number": pr_number,
        "html_url": html_url,
        "official": True,
    }


def _pull_request_number(title: str, message: str) -> int | None:
    text = f"{title}\n{message}"
    match = re.search(r"Merge pull request #(\d+)\b", text, flags=re.IGNORECASE)
    if match is None:
        match = re.search(r"\(#(\d+)\)\s*$", title.strip())
    return int(match.group(1)) if match else None


def _git(root: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(root), *args],
        check=False,
        capture_output=True,
        text=True,
        timeout=15,
    )
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip() or "git command failed"
        raise ValueError(detail)
    return result.stdout


def _git_optional(root: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(root), *args],
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )
    return result.stdout if result.returncode == 0 else ""
