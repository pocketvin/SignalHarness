"""Secure read-only HTTP snapshot collection for configured public web pages."""

from __future__ import annotations

import asyncio
import difflib
import hashlib
import ipaddress
import json
import socket
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlsplit

import httpx

from signal_harness.utils.fs import atomic_write_text

_MAX_REDIRECTS = 3
_DEFAULT_MAX_BYTES = 750_000
_ALLOWED_CONTENT_TYPES = (
    "text/",
    "application/json",
    "application/xml",
    "application/xhtml+xml",
)
_BLOCK_TAGS = {
    "article",
    "aside",
    "blockquote",
    "br",
    "dd",
    "div",
    "dl",
    "dt",
    "figcaption",
    "footer",
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
    "header",
    "li",
    "main",
    "nav",
    "ol",
    "p",
    "pre",
    "section",
    "table",
    "td",
    "th",
    "tr",
    "ul",
}
_SKIP_TAGS = {"script", "style", "noscript", "svg", "canvas", "template"}


class _VisibleTextParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._skip_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        del attrs
        lowered = tag.lower()
        if lowered in _SKIP_TAGS:
            self._skip_depth += 1
        elif lowered in _BLOCK_TAGS and self._skip_depth == 0:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        lowered = tag.lower()
        if lowered in _SKIP_TAGS and self._skip_depth:
            self._skip_depth -= 1
        elif lowered in _BLOCK_TAGS and self._skip_depth == 0:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if self._skip_depth == 0 and data.strip():
            self.parts.append(data)


def normalize_web_text(text: str, *, content_type: str = "text/html") -> str:
    """Normalize visible page text without executing page JavaScript."""

    if "html" in content_type.lower():
        parser = _VisibleTextParser()
        parser.feed(text)
        raw = "".join(parser.parts)
    else:
        raw = text
    lines: list[str] = []
    for line in raw.splitlines():
        normalized = " ".join(line.split())
        if normalized and (not lines or normalized != lines[-1]):
            lines.append(normalized)
    return "\n".join(lines).strip()


def web_snapshot_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def snapshot_diff_summary(previous: str, current: str, *, max_chars: int = 1200) -> str:
    """Return a bounded human-readable summary of replaced/added visible text."""

    before = previous.splitlines()
    after = current.splitlines()
    matcher = difflib.SequenceMatcher(a=before, b=after, autojunk=False)
    removed: list[str] = []
    added: list[str] = []
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag in {"replace", "delete"}:
            removed.extend(before[i1:i2])
        if tag in {"replace", "insert"}:
            added.extend(after[j1:j2])
        if sum(map(len, removed)) + sum(map(len, added)) >= max_chars:
            break
    old_text = " | ".join(removed[:4]).strip()
    new_text = " | ".join(added[:4]).strip()
    if not old_text and not new_text:
        return "Visible page content changed."
    parts = []
    if old_text:
        parts.append(f"Before: {old_text}")
    if new_text:
        parts.append(f"After: {new_text}")
    summary = "\n".join(parts)
    return summary[:max_chars].rstrip()


def _is_forbidden_ip(address: str) -> bool:
    ip = ipaddress.ip_address(address)
    return any(
        (
            ip.is_private,
            ip.is_loopback,
            ip.is_link_local,
            ip.is_multicast,
            ip.is_reserved,
            ip.is_unspecified,
        )
    )


def _validate_url_shape(url: str) -> tuple[str, int]:
    parsed = urlsplit(url)
    if parsed.scheme not in {"http", "https"}:
        raise ValueError("Web snapshot URL must use http or https")
    if parsed.username or parsed.password:
        raise ValueError("Web snapshot URL must not contain credentials")
    host = (parsed.hostname or "").strip().lower().rstrip(".")
    if not host:
        raise ValueError("Web snapshot URL must include a hostname")
    if host == "localhost" or host.endswith((".localhost", ".local", ".internal")):
        raise ValueError("Web snapshot URL must target a public hostname")
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    if port not in {80, 443}:
        raise ValueError("Web snapshot URL must use port 80 or 443")
    return host, port


async def assert_public_http_url(url: str) -> None:
    """Reject localhost/private/link-local targets before every outbound request."""

    host, port = _validate_url_shape(url)
    try:
        literal = ipaddress.ip_address(host)
    except ValueError:
        literal = None
    if literal is not None:
        if _is_forbidden_ip(str(literal)):
            raise ValueError("Web snapshot URL resolved to a non-public address")
        return
    try:
        infos = await asyncio.to_thread(socket.getaddrinfo, host, port, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise ValueError("Web snapshot hostname could not be resolved") from exc
    addresses = {str(info[4][0]).split("%", 1)[0] for info in infos if info[4]}
    if not addresses:
        raise ValueError("Web snapshot hostname resolved to no addresses")
    if any(_is_forbidden_ip(address) for address in addresses):
        raise ValueError("Web snapshot hostname resolved to a non-public address")


async def fetch_public_text(
    url: str, *, max_bytes: int = _DEFAULT_MAX_BYTES
) -> tuple[str, str, str]:
    """Fetch bounded text from a public URL, validating every redirect target."""

    current = url
    timeout = httpx.Timeout(15.0, connect=8.0)
    headers = {"User-Agent": "SignalHarness/0.1 (+read-only web change monitor)"}
    async with httpx.AsyncClient(
        timeout=timeout, follow_redirects=False, headers=headers
    ) as client:
        for redirect_count in range(_MAX_REDIRECTS + 1):
            await assert_public_http_url(current)
            async with client.stream("GET", current) as response:
                if response.status_code in {301, 302, 303, 307, 308}:
                    location = response.headers.get("location", "").strip()
                    if not location:
                        raise ValueError("Web snapshot redirect omitted Location")
                    if redirect_count >= _MAX_REDIRECTS:
                        raise ValueError("Web snapshot exceeded redirect limit")
                    current = urljoin(current, location)
                    continue
                response.raise_for_status()
                content_type = (
                    response.headers.get("content-type", "text/plain")
                    .split(";", 1)[0]
                    .strip()
                    .lower()
                )
                if not any(content_type.startswith(prefix) for prefix in _ALLOWED_CONTENT_TYPES):
                    raise ValueError(f"Web snapshot content type is not text: {content_type}")
                chunks: list[bytes] = []
                total = 0
                async for chunk in response.aiter_bytes():
                    total += len(chunk)
                    if total > max_bytes:
                        raise ValueError("Web snapshot exceeded size limit")
                    chunks.append(chunk)
                encoding = response.encoding or "utf-8"
                body = b"".join(chunks).decode(encoding, errors="replace")
                return current, content_type, body
    raise ValueError("Web snapshot request did not complete")


async def collect_web_change(
    *,
    url: str,
    source_name: str,
    state_dir: str | Path,
    official: bool = False,
    max_bytes: int = _DEFAULT_MAX_BYTES,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Create a baseline or emit one event when normalized visible content changes."""

    final_url, content_type, body = await fetch_public_text(url, max_bytes=max_bytes)
    normalized = normalize_web_text(body, content_type=content_type)
    if not normalized:
        raise ValueError("Web snapshot contained no visible text")
    current_hash = web_snapshot_hash(normalized)
    snapshot_root = Path(state_dir).expanduser().resolve() / "web_snapshots"
    snapshot_root.mkdir(parents=True, exist_ok=True)
    snapshot_id = hashlib.sha256(url.encode("utf-8")).hexdigest()[:20]
    snapshot_path = snapshot_root / f"{snapshot_id}.json"
    now = datetime.now(timezone.utc)
    previous: dict[str, Any] = {}
    if snapshot_path.is_file():
        try:
            loaded = json.loads(snapshot_path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                previous = loaded
        except (OSError, json.JSONDecodeError):
            previous = {}
    previous_hash = str(previous.get("content_hash") or "")
    previous_text = str(previous.get("text") or "")
    previous_observed_at = str(previous.get("observed_at") or "")
    payload = {
        "url": url,
        "final_url": final_url,
        "source_name": source_name,
        "content_hash": current_hash,
        "content_type": content_type,
        "text": normalized[:120_000],
        "observed_at": now.isoformat(),
    }
    atomic_write_text(snapshot_path, json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
    metadata = {
        "snapshot_path": str(snapshot_path),
        "content_hash": current_hash,
        "previous_hash": previous_hash or None,
        "baseline_created": not previous_hash,
        "changed": bool(previous_hash and previous_hash != current_hash),
        "final_url": final_url,
    }
    if not previous_hash or previous_hash == current_hash:
        return [], metadata
    summary = snapshot_diff_summary(previous_text, normalized)
    event = {
        "source_type": "web_change",
        "source_name": source_name,
        "title": f"{source_name} changed",
        "content": summary,
        "url": final_url,
        "published_at": now.isoformat(),
        "source_created_at": previous_observed_at or None,
        "source_updated_at": now.isoformat(),
        "change_kind": "updated",
        "official": official,
        "source_authority": "official" if official else "community",
        "previous_hash": previous_hash,
        "current_hash": current_hash,
        "previous_excerpt": previous_text[:600],
        "current_excerpt": normalized[:600],
    }
    return [event], metadata
