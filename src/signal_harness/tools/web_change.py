"""Fixture-backed and real read-only web change collection."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit, urlunsplit

import httpx
import yaml
from pydantic import BaseModel, Field

from signal_harness.resources import is_allowed_fixture_path, resolve_example_path
from signal_harness.runtime.tools_base import BaseTool, ToolExecutionContext, ToolResult
from signal_harness.tools.web_snapshot import collect_web_change


def _canonical_watchlist_url(value: str) -> str:
    parsed = urlsplit(value.strip())
    scheme = parsed.scheme.lower()
    host = (parsed.hostname or "").lower().rstrip(".")
    if not scheme or not host:
        return ""
    port = parsed.port
    default_port = (scheme == "https" and port in {None, 443}) or (
        scheme == "http" and port in {None, 80}
    )
    netloc = host if default_port else f"{host}:{port}"
    path = parsed.path or "/"
    return urlunsplit((scheme, netloc, path, parsed.query, ""))


def _approved_web_urls(context: ToolExecutionContext) -> set[str]:
    raw_path = context.metadata.get("watchlist_path")
    if not raw_path:
        return set()
    path = Path(str(raw_path)).expanduser().resolve()
    try:
        payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError):
        return set()
    if not isinstance(payload, dict):
        return set()
    web_changes = payload.get("web_changes")
    if not isinstance(web_changes, dict):
        return set()
    sources = web_changes.get("sources")
    if not isinstance(sources, list):
        return set()
    approved: set[str] = set()
    for source in sources:
        if not isinstance(source, dict):
            continue
        adapter = str(source.get("adapter") or "").strip().lower()
        url = str(source.get("url") or "").strip()
        if adapter in {"http", "snapshot"} and url:
            canonical = _canonical_watchlist_url(url)
            if canonical:
                approved.add(canonical)
    return approved


class WebChangeInput(BaseModel):
    action: Literal["load_mock_web_change_events", "load_fixture", "fetch_snapshot"] = (
        "load_fixture"
    )
    fixture: str = ""
    url: str = ""
    source_name: str = "web-change"
    official: bool = False
    max_bytes: int = Field(default=750_000, ge=1_024, le=1_000_000)


class WebChangeTool(BaseTool):
    """Load deterministic fixtures or compare a public HTTP(S) page snapshot."""

    name = "web_change"
    description = "Load fixture events or read and diff a configured public HTTP(S) page."
    input_model = WebChangeInput

    def is_read_only(self, arguments: WebChangeInput) -> bool:
        del arguments
        return True

    async def execute(self, arguments: WebChangeInput, context: ToolExecutionContext) -> ToolResult:
        if arguments.action == "fetch_snapshot":
            if not arguments.url.strip():
                return ToolResult(output="Web snapshot requires url", is_error=True)
            requested_url = _canonical_watchlist_url(arguments.url)
            if not requested_url or requested_url not in _approved_web_urls(context):
                return ToolResult(
                    output="Web snapshot URL is not approved by the current project Watchlist",
                    is_error=True,
                )
            state_dir = context.metadata.get("state_dir")
            if not state_dir:
                return ToolResult(output="Web snapshot state_dir is unavailable", is_error=True)
            try:
                events, metadata = await collect_web_change(
                    url=requested_url,
                    source_name=arguments.source_name.strip() or "web-change",
                    state_dir=str(state_dir),
                    official=arguments.official,
                    max_bytes=arguments.max_bytes,
                )
            except (ValueError, OSError, httpx.HTTPError) as exc:
                return ToolResult(output=f"Web snapshot failed: {exc}", is_error=True)
            return ToolResult(output=json.dumps(events, ensure_ascii=False), metadata=metadata)

        if not arguments.fixture.strip():
            return ToolResult(output="Fixture path is required", is_error=True)
        path = resolve_example_path(context.cwd, arguments.fixture)
        if not is_allowed_fixture_path(path, context.cwd):
            return ToolResult(output="Fixture must be inside the project workspace", is_error=True)
        if not path.exists():
            return ToolResult(output=f"Fixture not found: {path}", is_error=True)
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            return ToolResult(output=f"Fixture load failed: {exc}", is_error=True)
        if not isinstance(payload, list):
            return ToolResult(output="Fixture must contain a JSON list", is_error=True)
        return ToolResult(
            output=json.dumps(payload, ensure_ascii=False),
            metadata={"fixture": str(path), "event_count": len(payload)},
        )
