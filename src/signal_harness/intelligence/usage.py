"""Bounded source references in explicitly connected local roots; never execute code."""

from __future__ import annotations

import hashlib
import os
import re
from pathlib import Path
from typing import Any

from signal_harness.intelligence.contracts import UsageReference

_SKIP = {
    "node_modules",
    "vendor",
    "dist",
    "build",
    "outputs",
    "work",
    "Private-NoAI",
    "private-noai",
    "__pycache__",
    "venv",
}
_EXTENSIONS = {".py", ".ts", ".tsx", ".js", ".jsx", ".go", ".rs", ".java"}
_SECRET = re.compile(
    r"(?i)(api[_-]?key|authorization|bearer|password|secret|token)\s*[:=]|\b(?:sk-|ghp_|gho_)[A-Za-z0-9_-]{12,}"
)


def inspect_usage(roots: list[Path], entity: str) -> dict[str, Any]:
    """Collect conservative text/import references, not a call graph or reachability proof."""
    tokens = [v for v in re.findall(r"[A-Za-z][A-Za-z0-9_-]+", entity.casefold()) if len(v) >= 3]
    tokens = [
        v
        for v in tokens
        if v not in {"release", "releases", "official", "changelog", "github", "source", "project"}
    ][:5]
    pattern = (
        re.compile(r"(?<!\w)(?:" + "|".join(re.escape(v) for v in tokens) + r")(?!\w)", re.I)
        if tokens
        else None
    )
    refs: list[UsageReference] = []
    signature = hashlib.sha256()
    scanned = 0
    capped = False
    for approved in roots[:2]:
        # Do not resolve through a symlink into a different/private tree.
        if approved.is_symlink() or "private-noai" in {part.casefold() for part in approved.parts}:
            continue
        root = approved.resolve()
        if not root.is_dir() or "private-noai" in {part.casefold() for part in root.parts}:
            continue
        for current, dirs, files in os.walk(root, followlinks=False):
            dirs[:] = sorted(
                d
                for d in dirs
                if not d.startswith(".") and d not in _SKIP and not (Path(current) / d).is_symlink()
            )
            for name in sorted(files):
                path = Path(current) / name
                if name.startswith(".") or path.suffix not in _EXTENSIONS or path.is_symlink():
                    continue
                if not path.resolve().is_relative_to(root):
                    continue
                if scanned >= 240:
                    capped = True
                    break
                try:
                    if path.stat().st_size > 96000:
                        capped = True
                        continue
                    text = path.read_text(encoding="utf-8", errors="replace")
                except OSError:
                    continue
                relative = path.relative_to(root).as_posix()
                scanned += 1
                signature.update((str(root) + relative + text).encode())
                if pattern is None:
                    continue
                for line_number, line in enumerate(text.splitlines(), start=1):
                    if len(refs) >= 24:
                        break
                    if pattern.search(line) and not _SECRET.search(line):
                        refs.append(
                            UsageReference(
                                reference_id=f"usage-{len(refs) + 1}",
                                path=f"{root.name}/{relative}",
                                line=line_number,
                                excerpt=line.strip()[:260],
                                kind="import_reference"
                                if re.search(r"\b(import|require|from)\b", line)
                                else "text_reference",
                            )
                        )
            if scanned >= 240:
                break
    return {
        "references": [r.model_dump() for r in refs],
        "fingerprint": signature.hexdigest(),
        "files_checked": scanned,
        "limited": capped,
        "notice": (
            "仅在已授权连接的本地项目内做有限文本/导入匹配，不代表调用可达；没有执行代码或测试。"
            if scanned
            else "当前没有可检查的已授权本地源码；只能结合项目画像，不能声称已定位真实调用。"
        ),
    }
