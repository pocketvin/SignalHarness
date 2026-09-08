from __future__ import annotations

from pathlib import Path

import pytest

from signal_harness.projects.catalog import project_option
from signal_harness.projects.onboarding import (
    ProjectManifest,
    apply_project_draft,
    draft_project,
    inspect_project_directory,
    write_project_draft,
)


def test_inspect_python_project_builds_reviewable_profile_and_watchlist(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text(
        """[project]\nname = \"sample-agent-api\"\ndependencies = [\n  \"fastapi>=0.120\",\n  \"pydantic>=2\",\n  \"mcp>=2\",\n  \"httpx>=0.28\",\n]\n""",
        encoding="utf-8",
    )
    (tmp_path / "src" / "api").mkdir(parents=True)
    (tmp_path / "src" / "providers").mkdir(parents=True)

    draft = inspect_project_directory(tmp_path)

    assert draft.id == "sample-agent-api"
    assert draft.review_required is False
    assert {"fastapi", "pydantic", "mcp", "httpx"} <= set(draft.project_profile["dependencies"])
    assert "API service" in draft.project_profile["critical_modules"]
    repos = {item["repo"] for item in draft.watchlist["github"]["repositories"]}
    assert {"fastapi/fastapi", "pydantic/pydantic", "modelcontextprotocol/python-sdk"} <= repos
    web_urls = {item["url"] for item in draft.watchlist["web_changes"]["sources"]}
    assert "https://modelcontextprotocol.io/specification/latest" in web_urls


def test_browser_manifest_bundle_detects_node_stack_without_local_path_access() -> None:
    draft = draft_project(
        manifests=[
            ProjectManifest(
                path="package.json",
                content='{"name":"front-agent","dependencies":{"react":"^19","zod":"^4","@modelcontextprotocol/sdk":"^2"}}',
            )
        ],
        paths=["src/App.tsx", "src/mcp/client.ts"],
    )

    assert draft.id == "front-agent"
    assert "TypeScript / JavaScript" in draft.project_profile["tech_stack"]
    assert "MCP integration" in draft.project_profile["critical_modules"]
    repos = {item["repo"] for item in draft.watchlist["github"]["repositories"]}
    assert "facebook/react" in repos
    assert "modelcontextprotocol/typescript-sdk" in repos


def test_manifest_rejects_path_traversal() -> None:
    with pytest.raises(ValueError, match="relative"):
        ProjectManifest(path="../secret.env", content="secret")


def test_write_and_apply_project_draft_refuse_silent_overwrite(tmp_path: Path) -> None:
    draft = draft_project(
        manifests=[ProjectManifest(path="requirements.txt", content="fastapi\npydantic\n")],
        paths=["app/api.py", "tests/test_api.py"],
        name_hint="Example Service",
    )
    written = write_project_draft(draft, tmp_path / "drafts")
    assert all(path.is_file() for path in written.values())

    config = tmp_path / "configs"
    applied = apply_project_draft(draft, config)
    assert all(path.is_file() for path in applied.values())
    option = project_option(draft.id, config)
    assert option.name == "Example Service"
    with pytest.raises(FileExistsError, match="already exists"):
        apply_project_draft(draft, config)


def test_project_draft_cli_generates_json_and_requires_explicit_apply(
    tmp_path: Path,
    project_root: Path,
) -> None:
    import json
    import shutil
    from typer.testing import CliRunner
    from signal_harness.cli import app

    target = tmp_path / "target"
    target.mkdir()
    (target / "package.json").write_text(
        '{"name":"cli-agent","dependencies":{"fastify":"^5","zod":"^4"}}',
        encoding="utf-8",
    )
    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "project-draft",
            str(target),
            "--output-dir",
            str(tmp_path / "drafts"),
            "--json",
        ],
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["id"] == "cli-agent"
    assert payload["review_required"] is False
    assert payload["applied_files"] == {}

    config = tmp_path / "configs"
    shutil.copytree(project_root / "configs", config)
    applied = runner.invoke(
        app,
        [
            "project-draft",
            str(target),
            "--output-dir",
            str(tmp_path / "drafts-apply"),
            "--config-dir",
            str(config),
            "--apply",
            "--json",
        ],
    )
    assert applied.exit_code == 0, applied.output
    applied_payload = json.loads(applied.output)
    assert Path(applied_payload["applied_files"]["project"]).is_file()

    duplicate = runner.invoke(
        app,
        [
            "project-draft",
            str(target),
            "--config-dir",
            str(config),
            "--apply",
        ],
    )
    assert duplicate.exit_code != 0
    assert "already exists" in duplicate.output


def test_project_draft_service_accepts_manifest_bundle_without_server_path_read(
    project_root: Path,
    tmp_path: Path,
) -> None:
    from fastapi.testclient import TestClient
    from signal_harness.service import create_app

    app = create_app(
        cwd=project_root,
        output_dir=tmp_path / "outputs",
        state_dir=tmp_path / "state",
    )
    with TestClient(app) as client:
        response = client.post(
            "/project-drafts",
            json={
                "name_hint": "Browser Project",
                "manifests": [
                    {
                        "path": "package.json",
                        "content": '{"name":"browser-agent","dependencies":{"react":"^19","vite":"^7"}}',
                    }
                ],
                "paths": ["src/App.tsx", "src/main.tsx"],
            },
        )
        traversal = client.post(
            "/project-drafts",
            json={
                "manifests": [{"path": "../.env", "content": "SECRET=blocked"}],
                "paths": [],
            },
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["id"] == "browser-agent"
    assert payload["review_required"] is False
    assert "vitejs/vite" in {
        item["repo"] for item in payload["watchlist"]["github"]["repositories"]
    }
    assert traversal.status_code == 422


def test_project_draft_api_rejects_aggregate_manifest_payload(
    project_root: Path, tmp_path: Path
) -> None:
    from fastapi.testclient import TestClient

    from signal_harness.service import create_app

    app = create_app(
        cwd=project_root,
        output_dir=tmp_path / "outputs",
        state_dir=tmp_path / "state",
    )
    manifests = [
        {"path": "package.json", "content": "x" * 256_000},
        {"path": "pyproject.toml", "content": "y" * 256_001},
    ]
    with TestClient(app) as client:
        response = client.post("/project-drafts", json={"manifests": manifests, "paths": []})
    assert response.status_code == 422

def test_onboarding_uses_lockfile_versions_and_declared_purpose(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text(
        """[project]\nname = \"lock-demo\"\ndescription = \"Environment-aware API service\"\ndependencies = [\"fastapi>=0.116\", \"mcp>=2,<3\"]\n""",
        encoding="utf-8",
    )
    (tmp_path / "uv.lock").write_text(
        """version = 1\n[[package]]\nname = \"fastapi\"\nversion = \"0.116.2\"\n[[package]]\nname = \"mcp\"\nversion = \"2.12.0\"\n""",
        encoding="utf-8",
    )

    draft = inspect_project_directory(tmp_path)
    evidence = {item["name"]: item for item in draft.project_profile["dependency_evidence"]}

    assert draft.review_required is False
    assert draft.project_profile["purpose"] == "Environment-aware API service"
    assert draft.project_profile["onboarding"]["auto_active"] is True
    assert draft.project_profile["protocols"] == ["Model Context Protocol"]
    assert evidence["fastapi"]["declared"] == "fastapi>=0.116"
    assert evidence["fastapi"]["resolved_version"] == "0.116.2"
    assert "uv.lock" in evidence["fastapi"]["source_files"]
    assert evidence["fastapi"]["confidence"] == "verified"

def test_project_connect_auto_activates_profile_and_catalog(
    project_root: Path, tmp_path: Path
) -> None:
    import shutil
    from fastapi.testclient import TestClient

    from signal_harness.service import create_app

    config = tmp_path / "configs"
    shutil.copytree(project_root / "configs", config)
    app = create_app(
        cwd=project_root,
        config_dir=config,
        output_dir=tmp_path / "outputs",
        state_dir=tmp_path / "state",
    )
    with TestClient(app) as client:
        response = client.post(
            "/projects/connect",
            json={
                "name_hint": "Connected Demo",
                "manifests": [
                    {
                        "path": "pyproject.toml",
                        "content": "[project]\nname='connected-demo'\ndescription='Connected project'\ndependencies=['fastapi>=0.116']\n",
                    },
                    {
                        "path": "uv.lock",
                        "content": "version=1\n[[package]]\nname='fastapi'\nversion='0.116.2'\n",
                    },
                ],
                "paths": ["src/api.py", "tests/test_api.py"],
            },
        )
        assert response.status_code == 201, response.text
        payload = response.json()
        assert payload["auto_active"] is True
        assert payload["project"]["id"] == "connected-demo"
        assert payload["profile"]["profile_revision_id"].startswith("profile-")
        evidence = payload["profile"]["effective_profile"]["dependency_evidence"]
        assert evidence[0]["resolved_version"] == "0.116.2"

        meta = client.get("/demo/meta").json()
        assert "connected-demo" in {item["id"] for item in meta["projects"]}


def test_project_connect_cli_activates_profile_revision(
    tmp_path: Path, project_root: Path
) -> None:
    import json
    import shutil
    from typer.testing import CliRunner

    from signal_harness.cli import app

    target = tmp_path / "connected-cli"
    target.mkdir()
    (target / "pyproject.toml").write_text(
        "[project]\nname='connected-cli'\ndescription='CLI connected project'\ndependencies=['fastapi>=0.116']\n",
        encoding="utf-8",
    )
    (target / "uv.lock").write_text(
        "version=1\n[[package]]\nname='fastapi'\nversion='0.116.2'\n",
        encoding="utf-8",
    )
    config = tmp_path / "configs"
    shutil.copytree(project_root / "configs", config)
    state = tmp_path / "state"

    result = CliRunner().invoke(
        app,
        [
            "project-connect", str(target), "--config-dir", str(config),
            "--state-dir", str(state), "--json",
        ],
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["project_id"] == "connected-cli"
    assert payload["auto_active"] is True
    assert payload["profile"]["profile_revision_id"].startswith("profile-")
    assert payload["profile"]["effective_profile"]["dependency_evidence"][0]["resolved_version"] == "0.116.2"
    assert (config / "projects" / "connected-cli.yaml").is_file()
    assert (state / "projects" / "connected-cli" / "change_ledger.sqlite3").is_file()
