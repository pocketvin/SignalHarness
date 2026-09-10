"""Shared test fixtures."""

from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def isolate_github_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    """Never let ordinary tests consume the developer's GitHub CLI/keyring credential."""

    monkeypatch.setenv("GITHUB_TOKEN", "signalharness-test-token")
    monkeypatch.delenv("GH_TOKEN", raising=False)


@pytest.fixture
def project_root() -> Path:
    return Path(__file__).resolve().parents[1]
