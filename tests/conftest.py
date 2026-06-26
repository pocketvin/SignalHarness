"""Shared test fixtures."""

from __future__ import annotations

from pathlib import Path

import pytest
import pytest_asyncio

from openharness.tasks.manager import shutdown_task_manager


@pytest.fixture
def project_root() -> Path:
    return Path(__file__).resolve().parents[1]


@pytest_asyncio.fixture(autouse=True)
async def _reset_background_task_manager():
    yield
    await shutdown_task_manager()
