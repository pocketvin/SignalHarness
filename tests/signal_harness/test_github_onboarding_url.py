from __future__ import annotations

import asyncio
import base64
import json

import httpx
import pytest

from signal_harness.projects.github_onboarding import (
    draft_github_project,
    github_repository_from_url,
)


def test_github_repository_url_requires_repository_root() -> None:
    assert github_repository_from_url("https://github.com/Acme/Demo.git") == "Acme/Demo"
    with pytest.raises(ValueError):
        github_repository_from_url("https://example.com/acme/demo")
    with pytest.raises(ValueError):
        github_repository_from_url("https://github.com/acme/demo/issues/1")


def test_github_url_onboarding_reads_safe_manifests_and_marks_project_owned() -> None:
    package = json.dumps(
        {
            "name": "demo-agent",
            "description": "Demo Agent project",
            "dependencies": {"openai": "^1.0.0", "react": "^19.0.0"},
        }
    ).encode()
    blob = base64.b64encode(package).decode()

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/repos/Acme/Demo":
            return httpx.Response(
                200,
                json={
                    "full_name": "Acme/Demo",
                    "name": "Demo",
                    "default_branch": "main",
                    "description": "Repository description",
                    "html_url": "https://github.com/Acme/Demo",
                    "private": False,
                },
            )
        if path == "/repos/Acme/Demo/git/trees/main":
            assert request.url.params["recursive"] == "1"
            return httpx.Response(
                200,
                json={
                    "truncated": False,
                    "tree": [
                        {
                            "path": "package.json",
                            "type": "blob",
                            "sha": "blob-package",
                            "size": len(package),
                        },
                        {"path": "src/index.ts", "type": "blob", "sha": "code", "size": 100},
                    ],
                },
            )
        if path == "/repos/Acme/Demo/git/blobs/blob-package":
            return httpx.Response(200, json={"encoding": "base64", "content": blob})
        raise AssertionError(f"unexpected request: {request.url}")

    async def run():
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(handler),
            base_url="https://api.github.com",
        ) as client:
            return await draft_github_project("https://github.com/Acme/Demo", client=client)

    draft = asyncio.run(run())

    assert draft.id == "acme-demo"
    assert draft.name == "Demo"
    assert "openai" in draft.project_profile["dependencies"]
    assert draft.project_profile["repository"]["repo"] == "Acme/Demo"
    assert draft.project_profile["evidence"]["github_repository"] == "Acme/Demo"
    owned = next(
        item
        for item in draft.watchlist["github"]["repositories"]
        if item["repo"] == "Acme/Demo"
    )
    assert owned["project_owned"] is True
    assert {"commits", "pull_requests", "releases"}.issubset(set(owned["events"]))
    assert draft.evidence_files == ["package.json"]


def test_github_onboarding_prefers_root_manifest_over_nested_examples() -> None:
    root_package = json.dumps(
        {
            "name": "root-app",
            "description": "Root project purpose",
            "dependencies": {"openai": "^2.0.0"},
        }
    ).encode()
    nested_package = json.dumps(
        {
            "name": "example-app",
            "description": "Nested example purpose",
            "dependencies": {"react": "^19.0.0"},
        }
    ).encode()
    blobs = {
        "root": base64.b64encode(root_package).decode(),
        "nested": base64.b64encode(nested_package).decode(),
    }

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/repos/Acme/Rooted":
            return httpx.Response(
                200,
                json={
                    "full_name": "Acme/Rooted",
                    "name": "Rooted",
                    "default_branch": "main",
                    "description": "Repository metadata description",
                    "html_url": "https://github.com/Acme/Rooted",
                    "private": False,
                },
            )
        if path == "/repos/Acme/Rooted/git/trees/main":
            return httpx.Response(
                200,
                json={
                    "truncated": False,
                    "tree": [
                        {
                            "path": "examples/demo/package.json",
                            "type": "blob",
                            "sha": "nested",
                            "size": len(nested_package),
                        },
                        {
                            "path": "package.json",
                            "type": "blob",
                            "sha": "root",
                            "size": len(root_package),
                        },
                    ],
                },
            )
        if path == "/repos/Acme/Rooted/git/blobs/root":
            return httpx.Response(200, json={"encoding": "base64", "content": blobs["root"]})
        if path == "/repos/Acme/Rooted/git/blobs/nested":
            raise AssertionError("nested example manifest must not define the primary project")
        raise AssertionError(f"unexpected request: {request.url}")

    async def run():
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(handler),
            base_url="https://api.github.com",
        ) as client:
            return await draft_github_project("https://github.com/Acme/Rooted", client=client)

    draft = asyncio.run(run())

    assert draft.evidence_files == ["package.json"]
    assert draft.project_profile["purpose"] == "Root project purpose"
    assert "openai" in draft.project_profile["dependencies"]
    assert "react" not in draft.project_profile["dependencies"]
