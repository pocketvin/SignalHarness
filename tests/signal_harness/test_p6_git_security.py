from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

import httpx
import pytest

from signal_harness.persistence import ChangeLedger
from signal_harness.runtime.tools_base import ToolExecutionContext
from signal_harness.signal.normalizer import normalize_github_event, normalize_local_git_event
from signal_harness.signal.project_state import resolve_project_change_state
from signal_harness.signal.source_identity import git_change_identity, security_advisory_identity
from signal_harness.tools.local_git import LocalGitTool
from signal_harness.tools.security_osv import SecurityOsvTool


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
    _git(repo, "init")
    _git(repo, "config", "user.email", "signalharness@example.invalid")
    _git(repo, "config", "user.name", "SignalHarness Test")
    _git(repo, "remote", "add", "origin", "git@github.com:Acme/Demo.git")
    (repo / "README.md").write_text("first\n", encoding="utf-8")
    _git(repo, "add", "README.md")
    _git(repo, "commit", "-m", "feat: first change")
    return repo, _git(repo, "rev-parse", "HEAD")


@pytest.mark.asyncio
async def test_local_git_tool_preserves_repository_and_commit_identity(tmp_path: Path) -> None:
    repo, sha = _make_repo(tmp_path)
    tool = LocalGitTool()

    result = await tool.execute(
        tool.input_model(repo_path=str(repo)),
        ToolExecutionContext(cwd=tmp_path),
    )

    assert result.is_error is False
    rows = json.loads(result.output)
    assert rows[0]["sha"] == sha
    assert rows[0]["repository_identity"] == "acme/demo"
    assert rows[0]["html_url"] == f"https://github.com/acme/demo/commit/{sha}"
    assert result.metadata["coverage_status"] == "complete"
    event = normalize_local_git_event(rows[0])
    identity = git_change_identity(event)
    assert identity is not None
    assert identity.repository == "acme/demo"
    assert identity.commit_sha == sha


@pytest.mark.asyncio
async def test_local_git_history_cap_is_truthful(tmp_path: Path) -> None:
    repo, _ = _make_repo(tmp_path)
    (repo / "README.md").write_text("second\n", encoding="utf-8")
    _git(repo, "add", "README.md")
    _git(repo, "commit", "-m", "fix: second change")

    result = await LocalGitTool().execute(
        LocalGitTool.input_model(repo_path=str(repo), max_commits=1),
        ToolExecutionContext(cwd=tmp_path),
    )

    assert result.is_error is False
    assert len(json.loads(result.output)) == 1
    assert result.metadata["coverage_status"] == "partial"
    assert result.metadata["history_limited"] is True
    assert result.metadata["diagnostics"]


@pytest.mark.asyncio
async def test_osv_queries_exact_resolved_version_and_preserves_advisory_identity(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requests: list[dict[str, Any]] = []
    real_client = httpx.AsyncClient

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(json.loads(request.content.decode("utf-8")))
        return httpx.Response(
            200,
            json={
                "vulns": [
                    {
                        "id": "PYSEC-2026-999",
                        "aliases": ["GHSA-abcd-efgh-ijkl", "CVE-2026-9999"],
                        "summary": "Test vulnerability",
                        "details": "Affected exact resolved version.",
                        "published": "2026-09-01T00:00:00Z",
                        "modified": "2026-09-08T00:00:00Z",
                    }
                ]
            },
            request=request,
        )

    def factory(*args: object, **kwargs: object) -> httpx.AsyncClient:
        del args, kwargs
        return real_client(transport=httpx.MockTransport(handler))

    monkeypatch.setattr("signal_harness.tools.security_osv.httpx.AsyncClient", factory)
    result = await SecurityOsvTool().execute(
        SecurityOsvTool.input_model(
            dependencies=[{"name": "pydantic", "ecosystem": "PyPI", "version": "2.13.4"}]
        ),
        ToolExecutionContext(cwd=tmp_path),
    )

    assert result.is_error is False
    assert requests == [
        {"package": {"name": "pydantic", "ecosystem": "PyPI"}, "version": "2.13.4"}
    ]
    rows = json.loads(result.output)
    assert rows[0]["matched_package"] == "pydantic"
    assert rows[0]["matched_version"] == "2.13.4"
    assert result.metadata["coverage_status"] == "complete"

    from signal_harness.signal.normalizer import normalize_security_advisory_event

    event = normalize_security_advisory_event(rows[0])
    identity = security_advisory_identity(event)
    assert identity is not None
    assert identity.advisory_id == "CVE-2026-9999"
    state = resolve_project_change_state(
        event,
        {
            "dependencies": ["pydantic"],
            "dependency_evidence": [
                {"name": "pydantic", "resolved_version": "2.13.4", "ecosystem": "PyPI"}
            ],
        },
    )
    assert state.direct_dependency is True
    assert state.already_satisfied is False


def test_local_and_github_observations_of_same_commit_share_change_identity(tmp_path: Path) -> None:
    repo, sha = _make_repo(tmp_path)
    local_raw = {
        "sha": sha,
        "repository_identity": "acme/demo",
        "message": "feat: first change",
        "authored_at": "2026-09-08T10:00:00+00:00",
        "committed_at": "2026-09-08T10:00:00+00:00",
    }
    github_raw = {
        "sha": sha,
        "repository": "acme/demo",
        "html_url": f"https://github.com/acme/demo/commit/{sha}",
        "commit": {
            "message": "feat: first change",
            "author": {"date": "2026-09-08T10:00:00Z"},
            "committer": {"date": "2026-09-08T10:00:00Z"},
        },
    }
    local_event = normalize_local_git_event(local_raw)
    github_event = normalize_github_event(
        github_raw, repo="acme/demo", event_kind="github_commit"
    )
    ledger = ChangeLedger(tmp_path / "ledger.sqlite3")

    mapping = ledger.persist_observations([local_event, github_event])

    assert mapping[local_event.event_id][0] == mapping[github_event.event_id][0]
    assert repo.is_dir()
