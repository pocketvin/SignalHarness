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
async def test_github_merged_pull_fetch_uses_graphql_merge_identity_and_updated_cutoff(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    real_client = httpx.AsyncClient
    calls: list[httpx.Request] = []
    merge_sha = "c" * 40

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        body = json.loads(request.content)
        after = body["variables"]["after"]
        if after is None:
            nodes = [
                {
                    "id": "PR_kwDO10",
                    "number": 10,
                    "title": "Merge durable scan state",
                    "body": "Adds restart recovery.",
                    "url": "https://github.com/acme/demo/pull/10",
                    "createdAt": "2026-09-05T08:00:00Z",
                    "updatedAt": "2026-09-08T12:00:00Z",
                    "mergedAt": "2026-09-08T11:00:00Z",
                    "mergeCommit": {"oid": merge_sha},
                    "author": {"login": "alice"},
                    "authorAssociation": "MEMBER",
                    "mergedBy": {"login": "bob"},
                    "baseRefName": "main",
                    "headRefName": "durable-scan",
                    "labels": {"nodes": [{"name": "runtime"}]},
                },
                {
                    "id": "PR_kwDO11",
                    "number": 11,
                    "title": "Another recent merge",
                    "body": "Recent change.",
                    "url": "https://github.com/acme/demo/pull/11",
                    "createdAt": "2026-09-04T08:00:00Z",
                    "updatedAt": "2026-09-07T12:00:00Z",
                    "mergedAt": "2026-09-07T11:00:00Z",
                    "mergeCommit": {"oid": "e" * 40},
                    "author": {"login": "carol"},
                    "authorAssociation": "CONTRIBUTOR",
                    "mergedBy": {"login": "bob"},
                    "baseRefName": "main",
                    "headRefName": "recent-change",
                    "labels": {"nodes": []},
                },
            ]
            page_info = {"hasNextPage": True, "endCursor": "cursor-2"}
        else:
            nodes = [
                {
                    "id": "PR_kwDO8",
                    "number": 8,
                    "title": "Old merged PR",
                    "body": "Old change.",
                    "url": "https://github.com/acme/demo/pull/8",
                    "createdAt": "2026-08-29T08:00:00Z",
                    "updatedAt": "2026-08-30T12:00:00Z",
                    "mergedAt": "2026-08-30T11:00:00Z",
                    "mergeCommit": {"oid": "d" * 40},
                    "author": {"login": "dave"},
                    "authorAssociation": "CONTRIBUTOR",
                    "mergedBy": {"login": "bob"},
                    "baseRefName": "main",
                    "headRefName": "old-change",
                    "labels": {"nodes": []},
                }
            ]
            # Even with hasNextPage true, the updatedAt cutoff must stop here.
            page_info = {"hasNextPage": True, "endCursor": "cursor-3"}
        return httpx.Response(
            200,
            json={
                "data": {
                    "repository": {
                        "pullRequests": {"nodes": nodes, "pageInfo": page_info}
                    }
                }
            },
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
    assert len(calls) == 2
    assert all(str(request.url) == "https://api.github.com/graphql" for request in calls)
    assert json.loads(calls[0].content)["variables"]["after"] is None
    assert json.loads(calls[1].content)["variables"]["after"] == "cursor-2"
    rows = json.loads(result.output)
    assert [row["number"] for row in rows] == [10, 11]
    assert rows[0]["merge_commit_sha"] == merge_sha
    assert rows[0]["base_ref"] == "main"
    assert rows[0]["head_ref"] == "durable-scan"
    assert rows[0]["label_names"] == ["runtime"]
    assert rows[0]["author_login"] == "alice"
    assert rows[0]["merged_by_login"] == "bob"
    assert result.metadata["coverage_status"] == "complete"
    assert result.metadata["pages_fetched"] == 2
    assert result.metadata["identity_source"] == "graphql_merge_commit_oid"
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


@pytest.mark.asyncio
async def test_github_repository_discovery_is_bounded_and_preserves_provenance(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    real_client = httpx.AsyncClient
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(
            200,
            json={
                "total_count": 8,
                "incomplete_results": False,
                "items": [
                    {
                        "id": 123,
                        "node_id": "R_123",
                        "name": "sandbox-kit",
                        "full_name": "acme/sandbox-kit",
                        "description": "Tool execution sandbox for agent runtimes",
                        "html_url": "https://github.com/acme/sandbox-kit",
                        "homepage": "",
                        "created_at": "2026-09-05T00:00:00Z",
                        "updated_at": "2026-09-11T00:00:00Z",
                        "pushed_at": "2026-09-11T00:00:00Z",
                        "language": "Rust",
                        "topics": ["sandbox", "agent-runtime"],
                        "stargazers_count": 42,
                        "fork": False,
                        "archived": False,
                        "owner": {"login": "acme"},
                    }
                ],
            },
            request=request,
        )

    def factory(*args: object, **kwargs: object) -> httpx.AsyncClient:
        del args, kwargs
        return real_client(transport=httpx.MockTransport(handler))

    monkeypatch.setattr("signal_harness.tools.github_signal.httpx.AsyncClient", factory)
    result = await GitHubSignalTool().execute(
        GitHubSignalTool.input_model(
            action="search_repositories",
            query='"agent runtime" tools in:name,description,readme',
            since="2026-09-01T00:00:00Z",
            max_results=3,
        ),
        ToolExecutionContext(cwd=tmp_path),
    )

    assert result.is_error is False
    assert len(calls) == 1
    assert calls[0].url.path == "/search/repositories"
    assert '"agent runtime" tools' in calls[0].url.params["q"]
    assert "created:>=2026-09-01" in calls[0].url.params["q"]
    assert calls[0].url.params["per_page"] == "3"
    rows = json.loads(result.output)
    assert rows[0]["full_name"] == "acme/sandbox-kit"
    assert rows[0]["discovery_origin"] == "discovered"
    assert rows[0]["discovery_basis"] == '"agent runtime" tools in:name,description,readme'
    assert rows[0]["discovery_stars"] == 42
    assert result.metadata["coverage_status"] == "unknown"
    event = normalize_github_event(
        rows[0], repo=rows[0]["full_name"], event_kind="github_repository"
    )
    assert event.source_type == "github_repository"
    assert event.source_name == "acme/sandbox-kit"
    assert event.published_at == datetime(2026, 9, 5, tzinfo=timezone.utc)
    assert "Tool execution sandbox" in event.content
