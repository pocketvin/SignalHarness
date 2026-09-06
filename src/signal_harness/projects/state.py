"""Project-scoped persistent state paths with legacy-safe migration."""

from __future__ import annotations

import re
import shutil
from pathlib import Path

_PROJECT_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
_LEGACY_FILES = (
    "signal_memory.json",
    "feedback_memory.json",
    "alert_state.json",
    "policy_update_proposal.json",
    "signal_policy_update_proposal.json",
    "latest_learning_observation.json",
    "learning_staging.json",
    "applied_learning_changes.json",
    "replay_evaluation.json",
)


def validate_project_id(project_id: str) -> str:
    normalized = project_id.strip()
    if not _PROJECT_ID.fullmatch(normalized):
        raise ValueError("Invalid project id")
    return normalized


def project_state_dir(base_state_dir: str | Path, project_id: str) -> Path:
    root = Path(base_state_dir).expanduser().resolve()
    safe_id = validate_project_id(project_id)
    target = (root / "projects" / safe_id).resolve()
    target.relative_to(root)
    return target


def prepare_project_state(
    base_state_dir: str | Path,
    project_id: str,
    *,
    migrate_legacy_default: bool = False,
) -> Path:
    """Create one project's persistent state and optionally copy legacy default state.

    Migration is non-destructive: legacy files remain in place and are copied only when
    the project-scoped destination does not already exist.
    """

    root = Path(base_state_dir).expanduser().resolve()
    target = project_state_dir(root, project_id)
    target.mkdir(parents=True, exist_ok=True)
    if not migrate_legacy_default or project_id != "signalharness":
        return target

    for name in _LEGACY_FILES:
        source = root / name
        destination = target / name
        if source.is_file() and not destination.exists():
            shutil.copy2(source, destination)
    legacy_cache = root / "cache"
    target_cache = target / "cache"
    if legacy_cache.is_dir() and not target_cache.exists():
        shutil.copytree(legacy_cache, target_cache)
    return target
