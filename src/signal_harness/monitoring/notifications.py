"""Durable Inbox/Outbox notification flow with an optional signed webhook transport."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx

from signal_harness.alerts import AlertPolicy, select_alerts
from signal_harness.persistence import ChangeLedger
from signal_harness.signal.schemas import SignalAssessment, SignalEvent


@dataclass(frozen=True)
class SignedWebhookConfig:
    """Runtime-only webhook configuration; URL/secret are never persisted in the ledger."""

    url: str
    secret: str
    destination_key: str

    @classmethod
    def create(cls, *, url: str, secret: str) -> "SignedWebhookConfig":
        raw_url = url.strip()
        raw_secret = secret.strip()
        if not raw_url or not raw_secret:
            raise ValueError("signed webhook requires both URL and secret")
        parsed = httpx.URL(raw_url)
        if parsed.scheme not in {"http", "https"} or not parsed.host:
            raise ValueError("signed webhook URL must be absolute HTTP(S)")
        destination_key = hashlib.sha256(raw_url.encode("utf-8")).hexdigest()[:24]
        return cls(url=raw_url, secret=raw_secret, destination_key=destination_key)

    @classmethod
    def from_env(cls) -> "SignedWebhookConfig | None":
        url = os.environ.get("SIGNALHARNESS_WEBHOOK_URL", "").strip()
        secret = os.environ.get("SIGNALHARNESS_WEBHOOK_SECRET", "").strip()
        if not url and not secret:
            return None
        return cls.create(url=url, secret=secret)


class NotificationService:
    """Project-scoped notification projection over frozen ScanChange state."""

    def __init__(
        self,
        *,
        ledger: ChangeLedger,
        project_id: str,
        signal_policy: dict[str, Any],
        webhook: SignedWebhookConfig | None = None,
        max_delivery_attempts: int = 5,
    ) -> None:
        self.ledger = ledger
        self.project_id = project_id
        self.alert_policy = AlertPolicy.from_signal_policy(signal_policy)
        self.webhook = webhook
        self.max_delivery_attempts = max(1, max_delivery_attempts)

    def ingest_scan(self, *, scan_id: str) -> list[dict[str, Any]]:
        """Create durable Inbox items from high-priority frozen ScanChanges."""

        metadata = self.ledger.scan_metadata(scan_id)
        if metadata is None or metadata.get("project_id") != self.project_id:
            raise ValueError("scan does not belong to notification project")
        if metadata.get("status") != "success":
            return []

        created_items: list[dict[str, Any]] = []
        offset = 0
        while True:
            page = self.ledger.list_scan_changes(scan_id, offset=offset, limit=100)
            for row in page.items:
                assessment_raw = row.get("assessment")
                event_raw = row.get("event")
                if not isinstance(assessment_raw, dict) or not isinstance(event_raw, dict):
                    continue
                event = SignalEvent.model_validate(event_raw)
                assessment = SignalAssessment.model_validate(assessment_raw)
                selected = select_alerts(
                    [event],
                    [assessment],
                    policy=self.alert_policy,
                    already_alerted=set(),
                )
                if not selected:
                    continue
                alert = selected[0]
                event_revision_id = int(row["event_revision_id"])
                change_id = str(row["change_id"])
                notification_key = _notification_key(
                    change_id=change_id,
                    event_revision_id=event_revision_id,
                    decision=assessment.decision.value,
                )
                payload = {
                    "version": "signalharness-notification-v1",
                    "project_id": self.project_id,
                    "scan_id": scan_id,
                    "change_id": change_id,
                    "event_revision_id": event_revision_id,
                    "title": event.title,
                    "decision": assessment.decision.value,
                    "category": assessment.category.value,
                    "impact_score": assessment.impact_score,
                    "confidence": assessment.confidence,
                    "affected_modules": list(assessment.affected_modules),
                    "reasons": list(alert.get("reasons", [])),
                    "source": {
                        "type": event.source_type,
                        "name": event.source_name,
                        "url": event.url,
                    },
                    "links": {
                        "report": f"/runs/{scan_id}/report",
                        "product": f"/runs/{scan_id}/product",
                        "change": f"/runs/{scan_id}/changes/{change_id}",
                    },
                }
                item = self.ledger.create_inbox_item(
                    project_id=self.project_id,
                    scan_id=scan_id,
                    change_id=change_id,
                    event_revision_id=event_revision_id,
                    notification_key=notification_key,
                    title=event.title,
                    decision=assessment.decision.value,
                    category=assessment.category.value,
                    impact_score=assessment.impact_score,
                    payload=payload,
                )
                if item.pop("created", False):
                    created_items.append(item)
                if self.webhook is not None:
                    self._queue_webhook(item)
            if not page.has_more:
                break
            offset += len(page.items)
        return created_items

    def _queue_webhook(self, item: dict[str, Any]) -> dict[str, Any]:
        if self.webhook is None:
            raise RuntimeError("signed webhook is not configured")
        idempotency_key = hashlib.sha256(
            (
                f"{item['notification_key']}|signed_webhook|"
                f"{self.webhook.destination_key}"
            ).encode("utf-8")
        ).hexdigest()
        return self.ledger.enqueue_outbox(
            project_id=self.project_id,
            inbox_id=str(item["inbox_id"]),
            channel="signed_webhook",
            destination_key=self.webhook.destination_key,
            idempotency_key=idempotency_key,
        )

    async def deliver_due(
        self,
        *,
        now: datetime | None = None,
        client: httpx.AsyncClient | None = None,
    ) -> list[dict[str, Any]]:
        """Attempt due deliveries; retries reuse one durable Outbox identity."""

        if self.webhook is None:
            return []
        current = _utc(now or datetime.now(timezone.utc))
        due = self.ledger.due_outbox(project_id=self.project_id, now=current)
        if not due:
            return []
        owned_client = client is None
        active_client = client or httpx.AsyncClient(timeout=15, follow_redirects=False)
        results: list[dict[str, Any]] = []
        try:
            for outbox in due:
                inbox = self.ledger.inbox_item(
                    project_id=self.project_id,
                    inbox_id=str(outbox["inbox_id"]),
                )
                if inbox is None:
                    updated = self.ledger.finish_delivery_attempt(
                        project_id=self.project_id,
                        outbox_id=str(outbox["outbox_id"]),
                        status="failed",
                        http_status=None,
                        error="inbox item missing",
                        next_attempt_at=None,
                    )
                    results.append(updated)
                    continue
                body_payload = dict(inbox["payload"])
                body_payload["inbox_id"] = inbox["inbox_id"]
                body = json.dumps(
                    body_payload,
                    sort_keys=True,
                    separators=(",", ":"),
                    ensure_ascii=False,
                ).encode("utf-8")
                signature = hmac.new(
                    self.webhook.secret.encode("utf-8"),
                    body,
                    hashlib.sha256,
                ).hexdigest()
                http_status: int | None = None
                error = ""
                try:
                    response = await active_client.post(
                        self.webhook.url,
                        content=body,
                        headers={
                            "Content-Type": "application/json",
                            "User-Agent": "SignalHarness/0.1",
                            "X-SignalHarness-Signature": f"sha256={signature}",
                            "X-SignalHarness-Delivery": str(outbox["outbox_id"]),
                            "Idempotency-Key": str(outbox["idempotency_key"]),
                        },
                    )
                    http_status = response.status_code
                    if 200 <= response.status_code < 300:
                        status = "sent"
                        next_attempt = None
                    else:
                        error = f"webhook returned HTTP {response.status_code}"
                        status, next_attempt = self._retry_state(outbox, current)
                except httpx.HTTPError as exc:
                    error = f"{exc.__class__.__name__}: {exc}"
                    status, next_attempt = self._retry_state(outbox, current)
                updated = self.ledger.finish_delivery_attempt(
                    project_id=self.project_id,
                    outbox_id=str(outbox["outbox_id"]),
                    status=status,
                    http_status=http_status,
                    error=error,
                    next_attempt_at=next_attempt,
                )
                results.append(updated)
        finally:
            if owned_client:
                await active_client.aclose()
        return results

    def _retry_state(
        self, outbox: dict[str, Any], now: datetime
    ) -> tuple[str, datetime | None]:
        next_attempt_number = int(outbox["attempt_count"]) + 1
        if next_attempt_number >= self.max_delivery_attempts:
            return "failed", None
        minutes = min(60, 2**next_attempt_number)
        return "retry", now + timedelta(minutes=minutes)


def _notification_key(*, change_id: str, event_revision_id: int, decision: str) -> str:
    """A new source revision or decision escalation may create a new notification."""

    raw = f"{change_id}|{event_revision_id}|{decision}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)
