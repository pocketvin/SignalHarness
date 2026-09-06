from __future__ import annotations

from pathlib import Path

_DEFAULT_CONFIG = Path("configs")
_EXAMPLE_PREFIX = ("examples", "signal_harness")


def package_root() -> Path:
    return Path(__file__).resolve().parent


def source_checkout_root() -> Path:
    return package_root().parent.parent


def bundled_config_dir() -> Path:
    packaged = package_root() / "_resources" / "configs"
    if packaged.is_dir():
        return packaged
    source = source_checkout_root() / "configs"
    return source if source.is_dir() else packaged


def bundled_examples_dir() -> Path:
    packaged = package_root() / "_resources" / "examples" / "signal_harness"
    if packaged.is_dir():
        return packaged
    source = source_checkout_root() / "examples" / "signal_harness"
    return source if source.is_dir() else packaged


def resolve_config_dir(cwd: str | Path, value: str | Path = _DEFAULT_CONFIG) -> Path:
    """Prefer a workspace config directory; fall back only for the default path."""

    root = Path(cwd).expanduser().resolve()
    requested = Path(value).expanduser()
    target = requested.resolve() if requested.is_absolute() else (root / requested).resolve()
    if target.exists() or requested.is_absolute() or requested != _DEFAULT_CONFIG:
        return target
    return bundled_config_dir().resolve()


def resolve_example_path(cwd: str | Path, value: str | Path) -> Path:
    """Resolve package-owned default examples when they are absent from the workspace."""

    root = Path(cwd).expanduser().resolve()
    requested = Path(value).expanduser()
    target = requested.resolve() if requested.is_absolute() else (root / requested).resolve()
    if target.exists() or requested.is_absolute():
        return target
    parts = requested.parts
    if len(parts) >= 3 and tuple(parts[:2]) == _EXAMPLE_PREFIX:
        candidate = bundled_examples_dir().joinpath(*parts[2:]).resolve()
        if candidate.exists():
            return candidate
    return target


def is_allowed_fixture_path(path: str | Path, cwd: str | Path) -> bool:
    """Allow workspace fixtures plus immutable package-owned example fixtures only."""

    resolved = Path(path).expanduser().resolve()
    roots = (Path(cwd).expanduser().resolve(), bundled_examples_dir().resolve())
    for root in roots:
        try:
            resolved.relative_to(root)
            return True
        except ValueError:
            continue
    return False
