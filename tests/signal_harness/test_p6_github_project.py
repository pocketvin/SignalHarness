from __future__ import annotations

import json
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import httpx
import pytest

from signal_harness.agent_integration.mode import RunMode
from signal_harness.projects.onboarding import inspect_project_directory
from signal_harness.runtime.tools_base import ToolExecutionContext, ToolResult
from signal_harness.runtime.workflow import SignalHarnessWorkflow
from signal_harness.signal.normalizer import normalize_github_event
from signal_harness.signal.source_identity import git_change_identity
from signal_harness.tools.github_signal import GITHUB_API_VERSION, GitHubSignalTool


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def _make_repo(root: Path) -> tuple[Path, str]:
    repo = root / "repo"
    repo.mkdir()
    (repo / "pyproject.toml").write_text(
        "[project]\nname = 'demo-project'\nversion = '0.1.0'\ndependencies = []\n",
        encoding="utf-8",
    )
    _git(repo, "init")
    _git(repo, "config", "user.email", "signalharness@example.invalid")
    _git(repo, "config", "user.name", "SignalHarness Test")
    _git(repo, "remote", "add", "origin", "git@github.com:Acme/Demo.git")
    _git(repo, "add", "pyproject.toml")
    _git(repo, "commit", "-m", "feat: project change")
    return repo, _git(repo, "rev-parse", "HEAD")


@pytest.mark.asyncio
async def test_github_commit_fetch_uses_since_and_preserves_rich_metadata(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requests: list[httpx.Request] = []
    real_client = httpx.AsyncClient
    sha = "a" * 40

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            json=[
                {
                    "sha": sha,
                    "html_url": f"https://github.com/acme/demo/commit/{sha}",
                    "commit": {
                        "message": "feat: add project profile\n\nDetails",
                        "author": {"date": "2026-09-08T10:00:00Z"},
                        "committer": {"date": "2026-09-08T10:01:00Z"},
                        "verification": {
                            "verified": True,
                            "reason": "valid",
                            "verified_at": "2026-09-08T10:02:00Z",
                        },
                    },
                    "parents": [{"sha": "b" * 40}],
                    "author": {"login": "alice"},
                    "committer": {"login": "github-actions"},
                }
            ],
            request=request,
        )

    def factory(*args: object, **kwargs: object) -> httpx.AsyncClient:
        del args, kwargs
        return real_client(transport=httpx.MockTransport(handler))

    monkeypatch.setattr("signal_harness.tools.github_signal.httpx.AsyncClient", factory)
    since = datetime(2026, 9, 1, tzinfo=timezone.utc)
    result = await GitHubSignalTool().execute(
        GitHubSignalTool.input_model(
            action="fetch_repo_commits",
            repo="Acme/Demo",
            since=since,
        ),
        ToolExecutionContext(cwd=tmp_path),
    )

    assert result.is_error is False
    assert len(requests) == 1
    assert requests[0].url.path == "/repos/Acme/Demo/commits"
    assert requests[0].url.params["since"] == since.isoformat()
    assert requests[0].headers["X-GitHub-Api-Version"] == GITHUB_API_VERSION
    rows = json.loads(result.output)
    assert rows[0]["repository_identity"] == "acme/demo"
    assert rows[0]["parent_shas"] == ["b" * 40]
    assert rows[0]["author_login"] == "alice"
    assert rows[0]["committer_login"] == "github-actions"
    assert rows[0]["commit_verification"] == {
        "verified": True,
        "reason": "valid",
        "verified_at": "2026-09-08T10:02:00Z",
    }
    event = normalize_github_event(rows[0], repo="acme/demo", event_kind="github_commit")
    identity = git_change_identity(event)
    assert identity is not None
    assert identity.repository == "acme/demo"
    assert identity.commit_sha == sha


@pytest.mark.asyncio
async def test_github_merged_pull_fetch_filters_unmerged_and_stops_on_updated_cutoff(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    real_client = httpx.AsyncClient
    calls = 0
    merge_sha = "c" * 40

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(
                200,
                headers={"Link": '<https://api.github.com/repos/acme/demo/pulls?page=2>; rel="next"'},
                json=[
                    {
                        "id": 10,
                        "number": 10,
                        "title": "Merge durable scan state",
                        "body": "Adds restart recovery.",
                        "html_url": "https://github.com/acme/demo/pull/10",
                        "created_at": "2026-09-05T08:00:00Z",
                        "updated_at": "2026-09-08T12:00:00Z",
                        "merged_at": "2026-09-08T11:00:00Z",
                        "merge_commit_sha": merge_sha,
                        "user": {"login": "alice"},
                        "base": {"ref": "main"},
                        "head": {"ref": "durable-scan"},
                        "labels": [{"name": "runtime"}],
                    },
                    {
                        "id": 9,
                        "number": 9,
                        "title": "Closed without merge",
                        "updated_at": "2026-09-07T12:00:00Z",
                        "merged_at": None,
                        "merge_commit_sha": None,
                    },
                ],
                request=request,
            )
        return httpx.Response(
            200,
            headers={"Link": '<https://api.github.com/repos/acme/demo/pulls?page=3>; rel="next"'},
            json=[
                {
                    "id": 8,
                    "number": 8,
                    "title": "Old merged PR",
                    "updated_at": "2026-08-30T12:00:00Z",
                    "merged_at": "2026-08-30T11:00:00Z",
                    "merge_commit_sha": "d" * 40,
                }
            ],
            request=request,
        )

    def factory(*args: object, **kwargs: object) -> httpx.AsyncClient:
        del args, kwargs
        return real_client(transport=httpx.MockTransport(handler))

    monkeypatch.setattr("signal_harness.tools.github_signal.httpx.AsyncClient", factory)
    result = await GitHubSignalTool().execute(
        GitHubSignalTool.input_model(
            action="fetch_repo_merged_pulls",
            repo="acme/demo",
            since="2026-09-01T00:00:00Z",
        ),
        ToolExecutionContext(cwd=tmp_path),
    )

    assert result.is_error is False
    assert calls == 2
    rows = json.loads(result.output)
    assert [row["number"] for row in rows] == [10]
    assert rows[0]["base_ref"] == "main"
    assert rows[0]["head_ref"] == "durable-scan"
    assert rows[0]["label_names"] == ["runtime"]
    assert result.metadata["coverage_status"] == "complete"
    assert result.metadata["pages_fetched"] == 2
    event = normalize_github_event(rows[0], repo="acme/demo", event_kind="github_pull_request")
    identity = git_change_identity(event)
    assert identity is not None
    assert identity.commit_sha == merge_sha


def test_local_onboarding_discovers_own_github_repository(tmp_path: Path) -> None:
    repo, _ = _make_repo(tmp_path)

    draft = inspect_project_directory(repo)

    local = draft.watchlist["local_git"]["repositories"][0]
    assert local["github_repo"] == "acme/demo"
    own = next(
        item
        for item in draft.watchlist["github"]["repositories"]
        if item["repo"] == "acme/demo"
    )
    assert own["project_owned"] is True
    assert own["events"] == ["commits", "pull_requests"]


@pytest.mark.asyncio
async def test_workflow_aggregates_local_commit_github_commit_and_merged_pr(
    project_root: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo, sha = _make_repo(tmp_path)
    observed = (datetime.now(timezone.utc) - timedelta(seconds=2)).isoformat()

    async def fake_github_execute(
        self: GitHubSignalTool,
        arguments: Any,
        context: ToolExecutionContext,
    ) -> ToolResult:
        del self, context
        if arguments.action == "fetch_repo_commits":
            payload = [
                {
                    "sha": sha,
                    "repository": "acme/demo",
                    "repository_identity": "acme/demo",
                    "html_url": f"https://github.com/acme/demo/commit/{sha}",
                    "commit": {
                        "message": "feat: project change",
                        "author": {"date": observed},
                        "committer": {"date": observed},
                    },
                    "official": True,
                }
            ]
        elif arguments.action == "fetch_repo_merged_pulls":
            payload = [
                {
                    "id": 12,
                    "number": 12,
                    "title": "Project change PR",
                    "body": "Same source-control change.",
                    "repository": "acme/demo",
                    "repository_identity": "acme/demo",
                    "html_url": "https://github.com/acme/demo/pull/12",
                    "created_at": observed,
                    "updated_at": observed,
                    "merged_at": observed,
                    "merge_commit_sha": sha,
                    "official": True,
                }
            ]
        else:
            raise AssertionError(f"unexpected GitHub action: {arguments.action}")
        return ToolResult(
            output=json.dumps(payload),
            metadata={
                "coverage_status": "complete",
                "history_limited": False,
                "pages_fetched": 1,
                "diagnostics": [],
            },
        )

    monkeypatch.setattr(GitHubSignalTool, "execute", fake_github_execute)
    watchlist = tmp_path / "watchlist.yaml"
    watchlist.write_text(
        f"""local_git:
  repositories:
    - name: Demo
      path: {repo}
github:
  repositories:
    - repo: acme/demo
      project_owned: true
      events: [commits, pull_requests]
""",
        encoding="utf-8",
    )
    workflow = SignalHarnessWorkflow(
        cwd=repo,
        config_dir=project_root / "configs",
        project_profile_path=project_root / "configs/project_profile.yaml",
        watchlist_path=watchlist,
        output_dir=tmp_path / "outputs",
        state_dir=tmp_path / "state",
        mode=RunMode.DEMO,
        project_id="github-project-test",
    )

    result = await workflow.scan(max_events=10)

    assert result.coverage_status == "complete"
    assert result.all_change_count == 1
    assert {task.source_type for task in result.source_tasks} == {
        "local_git_commit",
        "github_commit",
        "github_pull_request",
    }
    rows = workflow.ledger.list_scan_changes(result.scan_id, offset=0, limit=10)
    assert rows.count == 1
    assert rows.items[0]["basic_relevance_score"] > 0
    assert rows.items[0]["event"]["raw_payload"]["project_owned"] is True
