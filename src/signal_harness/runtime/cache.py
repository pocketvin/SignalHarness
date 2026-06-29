"""Small local caches for source fetches, tool observations, and prompt metadata."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from signal_harness.utils.fs import atomic_write_text


def stable_hash(value: object) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class SourceFetchCacheEntry:
    key: str
    source_type: str
    source_name: str
    fetched_at: datetime
    ttl_seconds: int
    payload_hash: str
    payload: Any


class SourceFetchCache:
    """TTL JSON cache under `.signal-harness/cache/sources/`."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root).expanduser().resolve() / "sources"

    def key(
        self,
        tool_name: str,
        arguments: dict[str, Any],
    ) -> str:
        return stable_hash({"tool": tool_name, "arguments": arguments})

    def get(self, key: str) -> SourceFetchCacheEntry | None:
        path = self.root / f"{key}.json"
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            entry = SourceFetchCacheEntry(
                key=str(payload["key"]),
                source_type=str(payload["source_type"]),
                source_name=str(payload["source_name"]),
                fetched_at=datetime.fromisoformat(str(payload["fetched_at"])),
                ttl_seconds=int(payload["ttl_seconds"]),
                payload_hash=str(payload["payload_hash"]),
                payload=payload["payload"],
            )
        except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError):
            return None
        now = datetime.now(timezone.utc)
        fetched = entry.fetched_at
        if fetched.tzinfo is None:
            fetched = fetched.replace(tzinfo=timezone.utc)
        if (now - fetched).total_seconds() > entry.ttl_seconds:
            return None
        return entry

    def put(
        self,
        *,
        key: str,
        source_type: str,
        source_name: str,
        ttl_seconds: int,
        payload: Any,
    ) -> None:
        try:
            self.root.mkdir(parents=True, exist_ok=True)
            body = {
                "key": key,
                "source_type": source_type,
                "source_name": source_name,
                "fetched_at": datetime.now(timezone.utc).isoformat(),
                "ttl_seconds": ttl_seconds,
                "payload_hash": stable_hash(payload),
                "payload": payload,
            }
            atomic_write_text(
                self.root / f"{key}.json",
                json.dumps(body, ensure_ascii=False, indent=2, default=str) + "\n",
            )
        except OSError:
            return


class ToolObservationCache:
    """Per-run deduplication for identical read-only tool requests."""

    def __init__(self) -> None:
        self._values: dict[str, Any] = {}

    def key(self, tool_name: str, arguments: dict[str, Any]) -> str:
        return stable_hash({"tool_name": tool_name, "arguments": arguments})

    def get(self, key: str) -> Any | None:
        return self._values.get(key)

    def put(self, key: str, value: Any) -> None:
        self._values[key] = value


@dataclass(frozen=True)
class PromptPrefixCacheMetadata:
    prompt_prefix_hash: str
    static_context_hash: str
    dynamic_context_hash: str
    prompt_version: str
    provider: str
    model: str
