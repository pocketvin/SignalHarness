from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient

from signal_harness.projects.architecture_snapshot import (
    ArchitectureSourceSample,
    architecture_usage_references,
    derive_architecture_snapshot,
)
from signal_harness.projects.onboarding import ProjectManifest, draft_project, inspect_project_directory
from signal_harness.service import create_app


def test_architecture_source_sample_rejects_traversal_and_private_paths() -> None:
    with pytest.raises(ValueError):
        ArchitectureSourceSample(path="../secret.py", content="import os")
    with pytest.raises(ValueError):
        ArchitectureSourceSample(path="Private-NoAI/secret.py", content="import os")


def test_architecture_snapshot_keeps_static_import_evidence_and_edges() -> None:
    profile = {"dependencies": ["openai", "zod"]}
    samples = [
        ArchitectureSourceSample(
            path="src/server.ts",
            content='import OpenAI from "openai";\nimport { schema } from "./schema.js";\n',
        ),
        ArchitectureSourceSample(
            path="src/schema.ts",
            content='import { z } from "zod";\nexport const schema = z.object({});\n',
        ),
    ]
    snapshot = derive_architecture_snapshot(
        profile,
        ["src/server.ts", "src/schema.ts"],
        samples,
    )

    assert snapshot["coverage"] == "source_sampled"
    assert {item["dependency"] for item in snapshot["dependency_usage"]} == {"openai", "zod"}
    assert {tuple((item["from"], item["to"])) for item in snapshot["static_edges"]} == {
        ("src/server.ts", "src/schema.ts")
    }
    openai = next(item for item in snapshot["dependency_usage"] if item["dependency"] == "openai")
    assert openai["references"][0]["line"] == 1
    assert openai["references"][0]["excerpt"] == 'import OpenAI from "openai";'


def test_architecture_usage_references_map_repository_entity_to_dependency() -> None:
    profile = {
        "architecture_snapshot": {
            "dependency_usage": [
                {
                    "dependency": "zod",
                    "references": [
                        {
                            "path": "src/tools/schema.ts",
                            "line": 12,
                            "excerpt": 'import { z } from "zod";',
                        }
                    ],
                }
            ]
        }
    }
    refs = architecture_usage_references(profile, "colinhacks/zod")
    assert refs == [
        {
            "reference_id": "arch-usage-1",
            "path": "src/tools/schema.ts",
            "line": 12,
            "excerpt": 'import { z } from "zod";',
            "kind": "architecture_import_reference",
        }
    ]


def test_architecture_usage_references_map_scoped_package_to_repository() -> None:
    profile = {
        "architecture_snapshot": {
            "dependency_usage": [
                {
                    "dependency": "@modelcontextprotocol/sdk",
                    "references": [
                        {
                            "path": "src/mcp/server.ts",
                            "line": 7,
                            "excerpt": 'import { Server } from "@modelcontextprotocol/sdk/server";',
                        }
                    ],
                }
            ]
        }
    }

    refs = architecture_usage_references(profile, "modelcontextprotocol/typescript-sdk")

    assert len(refs) == 1
    assert refs[0]["path"] == "src/mcp/server.ts"
    assert refs[0]["line"] == 7


def test_local_project_onboarding_builds_bounded_architecture_snapshot(tmp_path: Path) -> None:
    (tmp_path / "package.json").write_text(
        json.dumps(
            {
                "name": "gateway-demo",
                "description": "Multi-provider gateway",
                "dependencies": {"express": "^5", "openai": "^1", "zod": "^4"},
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / "src" / "providers").mkdir(parents=True)
    (tmp_path / "src" / "server.ts").write_text(
        'import express from "express";\nimport { run } from "./providers/openai";\n',
        encoding="utf-8",
    )
    (tmp_path / "src" / "providers" / "openai.ts").write_text(
        'import OpenAI from "openai";\nimport { z } from "zod";\nexport const run = () => new OpenAI();\n',
        encoding="utf-8",
    )

    draft = inspect_project_directory(tmp_path)
    snapshot = draft.project_profile["architecture_snapshot"]

    assert snapshot["coverage"] == "source_sampled"
    assert snapshot["source_files_sampled"] >= 2
    assert {item["dependency"] for item in snapshot["dependency_usage"]} >= {
        "express",
        "openai",
        "zod",
    }
    assert any(item["path"] == "src/server.ts" for item in snapshot["entrypoints"])
    assert any(item["name"] == "模型 / Provider" for item in snapshot["subsystems"])


def test_architecture_refresh_updates_only_snapshot_and_profile_revision(
    project_root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = tmp_path / "configs"
    shutil.copytree(project_root / "configs", config)
    profile_path = config / "project_profiles" / "openclaw-openclaw.yaml"
    current = yaml.safe_load(profile_path.read_text(encoding="utf-8"))
    current["custom_review_note"] = "keep-me"
    current.pop("architecture_snapshot", None)
    profile_path.write_text(
        yaml.safe_dump(current, allow_unicode=True, sort_keys=False), encoding="utf-8"
    )
    watchlist_path = config / "watchlists" / "openclaw-openclaw.yaml"
    watchlist_before = watchlist_path.read_text(encoding="utf-8")

    refreshed = draft_project(
        manifests=[
            ProjectManifest(
                path="package.json",
                content=json.dumps(
                    {
                        "name": "openclaw",
                        "description": "test gateway",
                        "dependencies": {"express": "^5"},
                    }
                ),
            )
        ],
        paths=["src/server.ts"],
        name_hint="openclaw",
        source_samples=[
            ArchitectureSourceSample(
                path="src/server.ts", content='import express from "express";\n'
            )
        ],
    )

    async def fake_refresh(url: str):
        assert url == "https://github.com/openclaw/openclaw"
        return refreshed

    monkeypatch.setattr("signal_harness.environment_application.draft_github_project", fake_refresh)
    app = create_app(
        cwd=project_root,
        config_dir=config,
        output_dir=tmp_path / "outputs",
        state_dir=tmp_path / "state",
    )
    with TestClient(app) as client:
        before = client.get("/projects/openclaw-openclaw/profile").json()
        response = client.post("/projects/openclaw-openclaw/architecture/refresh")
        assert response.status_code == 200, response.text
        after = response.json()

    assert before["profile_revision_id"] != after["profile_revision_id"]
    assert after["effective_profile"]["architecture_snapshot"]["coverage"] == "source_sampled"
    saved = yaml.safe_load(profile_path.read_text(encoding="utf-8"))
    assert saved["custom_review_note"] == "keep-me"
    assert saved["purpose"] == current["purpose"]
    assert saved["architecture_snapshot"]["dependency_usage"][0]["dependency"] == "express"
    assert watchlist_path.read_text(encoding="utf-8") == watchlist_before


def test_architecture_refresh_rejects_project_without_github_repository(
    project_root: Path, tmp_path: Path
) -> None:
    app = create_app(
        cwd=project_root,
        output_dir=tmp_path / "outputs",
        state_dir=tmp_path / "state",
    )
    with TestClient(app) as client:
        response = client.post("/projects/signalharness/architecture/refresh")
    assert response.status_code == 409
