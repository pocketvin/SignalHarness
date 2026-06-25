"""RSS and Atom feed collection through an OpenHarness tool."""

from __future__ import annotations

import json
import xml.etree.ElementTree as ET
from typing import Any, Literal

import httpx
from pydantic import BaseModel, model_validator

from openharness.tools.base import BaseTool, ToolExecutionContext, ToolResult
from signal_harness.signal.normalizer import normalize_rss_item


class RssSignalInput(BaseModel):
    action: Literal["fetch_feed", "normalize_rss_item"]
    url: str = ""
    feed_name: str = ""
    raw: dict[str, Any] | None = None

    @model_validator(mode="after")
    def _validate_action_inputs(self) -> RssSignalInput:
        if self.action == "fetch_feed" and not self.url.startswith(("http://", "https://")):
            raise ValueError("url must be HTTP or HTTPS")
        if self.action == "normalize_rss_item" and self.raw is None:
            raise ValueError("raw is required for normalization")
        return self


class RssSignalTool(BaseTool):
    """Fetch a feed or normalize one parsed item."""

    name = "rss_signal"
    description = "Fetch an RSS/Atom feed or normalize one feed item."
    input_model = RssSignalInput

    def is_read_only(self, arguments: RssSignalInput) -> bool:
        return True

    async def execute(self, arguments: RssSignalInput, context: ToolExecutionContext) -> ToolResult:
        del context
        if arguments.action == "normalize_rss_item":
            assert arguments.raw is not None
            event = normalize_rss_item(arguments.raw, feed_name=arguments.feed_name or None)
            return ToolResult(output=event.model_dump_json())
        try:
            async with httpx.AsyncClient(timeout=20, follow_redirects=True) as client:
                response = await client.get(
                    arguments.url,
                    headers={"User-Agent": "SignalHarness/0.1"},
                )
                response.raise_for_status()
        except httpx.HTTPError as exc:
            return ToolResult(output=f"RSS request failed: {exc}", is_error=True)
        try:
            items = parse_feed(response.text)
        except (ET.ParseError, ValueError) as exc:
            return ToolResult(output=f"RSS parse failed: {exc}", is_error=True)
        return ToolResult(output=json.dumps(items, ensure_ascii=False))


def parse_feed(xml_text: str) -> list[dict[str, str]]:
    """Parse common RSS 2.0 and Atom shapes without an extra dependency."""

    root = ET.fromstring(xml_text)
    items: list[dict[str, str]] = []
    if _local(root.tag) == "feed":
        for entry in root:
            if _local(entry.tag) != "entry":
                continue
            links = [
                child.attrib.get("href", "")
                for child in entry
                if _local(child.tag) == "link"
            ]
            items.append(
                {
                    "title": _child_text(entry, "title"),
                    "summary": _child_text(entry, "summary") or _child_text(entry, "content"),
                    "link": next((link for link in links if link), ""),
                    "published": _child_text(entry, "published") or _child_text(entry, "updated"),
                    "id": _child_text(entry, "id"),
                }
            )
    else:
        for item in root.iter():
            if _local(item.tag) != "item":
                continue
            items.append(
                {
                    "title": _child_text(item, "title"),
                    "summary": _child_text(item, "description"),
                    "link": _child_text(item, "link"),
                    "published": _child_text(item, "pubDate"),
                    "id": _child_text(item, "guid"),
                }
            )
    return items


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _child_text(element: ET.Element, name: str) -> str:
    for child in element:
        if _local(child.tag) == name:
            return "".join(child.itertext()).strip()
    return ""
