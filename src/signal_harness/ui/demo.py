from __future__ import annotations

from functools import lru_cache
from pathlib import Path


def demo_asset_dir() -> Path:
    """Return the package-owned Golden Demo static asset directory."""

    return Path(__file__).resolve().parent / "static"


@lru_cache(maxsize=1)
def render_demo_page() -> str:
    """Return the dependency-free live change radar, Chinese by default."""

    return (demo_asset_dir() / "demo.html").read_text(encoding="utf-8")
