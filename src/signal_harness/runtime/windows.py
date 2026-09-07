"""Resolve user-facing Scan windows without conflating source cursors or schedules."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Literal, Protocol

WindowMode = Literal["unbounded", "since_last", "24h", "7d", "30d", "custom", "legacy"]


class QueryCheckpointReader(Protocol):
    def get_interactive_checkpoint(
        self, *, project_id: str, consumer_id: str = "local-owner"
    ) -> datetime | None: ...


@dataclass(frozen=True)
class ResolvedScanWindow:
    mode: WindowMode
    lower: datetime | None
    upper: datetime
    first_use: bool = False
    checkpoint_eligible: bool = False

    def public_payload(self) -> dict[str, object]:
        return {
            "mode": self.mode,
            "from": self.lower.isoformat() if self.lower else None,
            "to": self.upper.isoformat(),
            "first_use": self.first_use,
            "checkpoint_eligible": self.checkpoint_eligible,
        }


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def resolve_scan_window(
    *,
    ledger: QueryCheckpointReader,
    project_id: str,
    mode: WindowMode = "unbounded",
    now: datetime | None = None,
    custom_from: datetime | None = None,
    custom_to: datetime | None = None,
    legacy_since: datetime | None = None,
    consumer_id: str = "local-owner",
) -> ResolvedScanWindow:
    """Freeze one [L,U) interval. `since_last` uses only the interactive checkpoint."""

    upper = _utc(custom_to or now or datetime.now(timezone.utc))
    if mode == "unbounded":
        return ResolvedScanWindow(mode=mode, lower=None, upper=upper)
    if mode == "legacy":
        lower = _utc(legacy_since) if legacy_since else None
        return ResolvedScanWindow(mode=mode, lower=lower, upper=upper)
    if mode == "since_last":
        checkpoint = ledger.get_interactive_checkpoint(
            project_id=project_id, consumer_id=consumer_id
        )
        first_use = checkpoint is None
        lower = checkpoint or (upper - timedelta(days=7))
        return ResolvedScanWindow(
            mode=mode,
            lower=_utc(lower),
            upper=upper,
            first_use=first_use,
            checkpoint_eligible=True,
        )
    if mode in {"24h", "7d", "30d"}:
        days = {"24h": 1, "7d": 7, "30d": 30}[mode]
        return ResolvedScanWindow(
            mode=mode,
            lower=upper - timedelta(days=days),
            upper=upper,
            checkpoint_eligible=True,
        )
    if mode == "custom":
        if custom_from is None:
            raise ValueError("custom window requires custom_from")
        lower = _utc(custom_from)
        if lower >= upper:
            raise ValueError("custom window requires from < to")
        now_utc = _utc(now or datetime.now(timezone.utc))
        ends_now = abs((upper - now_utc).total_seconds()) <= 1
        return ResolvedScanWindow(
            mode=mode,
            lower=lower,
            upper=upper,
            checkpoint_eligible=ends_now,
        )
    raise ValueError(f"Unsupported scan window mode: {mode}")
