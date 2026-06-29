"""Local alert de-duplication state."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from signal_harness.utils.fs import atomic_write_text


def load_alerted_event_ids(path: str | Path) -> set[str]:
    target = Path(path).expanduser().resolve()
    if not target.exists():
        return set()
    payload = json.loads(target.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        return set()
    raw_ids = payload.get("alerted_event_ids", [])
    if not isinstance(raw_ids, list):
        return set()
    return {str(item) for item in raw_ids}


def save_alert_state(
    path: str | Path,
    *,
    alerted_event_ids: set[str],
    latest_alerts: list[dict[str, Any]],
) -> Path:
    target = Path(path).expanduser().resolve()
    payload = {
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "alerted_event_ids": sorted(alerted_event_ids),
        "latest_alert_count": len(latest_alerts),
        "latest_alert_ids": [str(item.get("event_id")) for item in latest_alerts],
    }
    atomic_write_text(
        target,
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
    )
    return target

