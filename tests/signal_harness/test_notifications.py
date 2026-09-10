from __future__ import annotations

import hashlib
import hmac
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
import pytest

from signal_harness.monitoring.notifications import NotificationService, SignedWebhookConfig
from signal_harness.persistence import ChangeLedger
from signal_harness.signal.policy import load_signal_policy, load_yaml_mapping
from signal_harness.signal.schemas import (
    SignalAssessment,
    SignalCategory,
    SignalDecision,
    SignalEvent,
    SourceQuality,
)


def _event(*, content: str = "A vulnerable provider permission path was disclosed.") -> SignalEvent:
    now = datetime(2026, 9, 9, 12, 0, tzinfo=timezone.utc)
    return SignalEvent(
        event_id="OSV-DEMO-2026-1",
        source_type="security_advisory",
        source_name="OSV",
        title="Critical dependency vulnerability",
        content=content,
        url="https://osv.dev/vulnerability/OSV-DEMO-2026-1",
        published_at=now - timedelta(hours=1),
        raw_payload={
            "id": "OSV-DEMO-2026-1",
            "aliases": ["CVE-2026-9999"],
            "official": True,
            "matched_package": "pydantic",
            "matched_version": "2.13.4",
        },
        collected_at=now,
    )


def _assessment(decision: SignalDecision) -> SignalAssessment:
    return SignalAssessment(
        event_id="OSV-DEMO-2026-1",
        category=SignalCategory.SECURITY_SUPPLY_CHAIN,
        relevance_score=95,
        impact_score=96 if decision is SignalDecision.ACTION_REQUIRED else 90,
        confidence=0.93,
        affected_modules=["security", "provider validation"],
        evidence_urls=["https://osv.dev/vulnerability/OSV-DEMO-2026-1"],
        source_quality=SourceQuality.OFFICIAL,
        reason="The exact resolved dependency version is affected.",
        action_items=["Review and upgrade the affected dependency."],
        decision=decision,
        cross_source_confidence=0.8,
    )


def _persist_scan(
    *,
    ledger: ChangeLedger,
    project_root: Path,
    scan_id: str,
    event: SignalEvent,
    assessment: SignalAssessment,
) -> dict[str, object]:
    profile = load_yaml_mapping(project_root / "configs/project_profile.yaml")
    policy = load_signal_policy(project_root / "configs/signal_policy.yaml")
    ledger.begin_scan(
        scan_id=scan_id,
        project_id="signalharness",
        collected_count=1,
        deduped_count=1,
    )
    refs = ledger.persist_observations([event])
    ledger.freeze_scan_changes(
        scan_id=scan_id,
        project_id="signalharness",
        events=[event],
        event_change_ids=refs,
        project_profile=profile,
        policy=policy,
        analyzed_event_ids={event.event_id},
        assessments=[assessment],
    )
    ledger.complete_scan(
        scan_id=scan_id,
        analyzed_count=1,
        relevant_count=1,
        coverage_status="complete",
    )
    return ledger.list_scan_changes(scan_id, limit=10).items[0]


def _service(
    ledger: ChangeLedger,
    project_root: Path,
    *,
    webhook: SignedWebhookConfig | None = None,
) -> NotificationService:
    return NotificationService(
        ledger=ledger,
        project_id="signalharness",
        signal_policy=load_signal_policy(project_root / "configs/signal_policy.yaml"),
        webhook=webhook,
    )


def test_inbox_deduplicates_same_projection_but_allows_revision_and_decision_escalation(
    project_root: Path,
    tmp_path: Path,
) -> None:
    ledger = ChangeLedger(tmp_path / "ledger.sqlite3")
    first_event = _event()
    first_row = _persist_scan(
        ledger=ledger,
        project_root=project_root,
        scan_id="scan-alert",
        event=first_event,
        assessment=_assessment(SignalDecision.ALERT),
    )
    service = _service(ledger, project_root)

    created = service.ingest_scan(scan_id="scan-alert")
    assert len(created) == 1
    assert service.ingest_scan(scan_id="scan-alert") == []

    escalated_row = _persist_scan(
        ledger=ledger,
        project_root=project_root,
        scan_id="scan-action",
        event=first_event,
        assessment=_assessment(SignalDecision.ACTION_REQUIRED),
    )
    escalated = service.ingest_scan(scan_id="scan-action")
    assert len(escalated) == 1
    assert first_row["event_revision_id"] == escalated_row["event_revision_id"]

    revised_event = _event(content="Updated advisory adds a confirmed exploit path.")
    revised_row = _persist_scan(
        ledger=ledger,
        project_root=project_root,
        scan_id="scan-revision",
        event=revised_event,
        assessment=_assessment(SignalDecision.ACTION_REQUIRED),
    )
    revised = service.ingest_scan(scan_id="scan-revision")
    assert len(revised) == 1
    assert revised_row["change_id"] == first_row["change_id"]
    assert revised_row["event_revision_id"] != first_row["event_revision_id"]

    inbox = ledger.list_inbox(project_id="signalharness")
    assert len(inbox) == 3
    assert {item["decision"] for item in inbox} == {"alert", "action_required"}
    assert len({item["notification_key"] for item in inbox}) == 3

    unread = ledger.list_inbox(project_id="signalharness", unread_only=True)
    assert len(unread) == 3
    assert ledger.mark_inbox_read(
        project_id="signalharness", inbox_id=str(unread[0]["inbox_id"])
    )
    assert len(ledger.list_inbox(project_id="signalharness", unread_only=True)) == 2


@pytest.mark.asyncio
async def test_signed_webhook_retries_one_outbox_identity_and_records_attempts(
    project_root: Path,
    tmp_path: Path,
) -> None:
    ledger = ChangeLedger(tmp_path / "ledger.sqlite3")
    _persist_scan(
        ledger=ledger,
        project_root=project_root,
        scan_id="scan-webhook",
        event=_event(),
        assessment=_assessment(SignalDecision.ACTION_REQUIRED),
    )
    webhook = SignedWebhookConfig.create(
        url="https://notifications.example.test/signalharness",
        secret="test-shared-secret",
    )
    service = _service(ledger, project_root, webhook=webhook)
    assert len(service.ingest_scan(scan_id="scan-webhook")) == 1
    # Re-ingestion is idempotent for both Inbox and Outbox.
    assert service.ingest_scan(scan_id="scan-webhook") == []

    now = datetime.now(timezone.utc) + timedelta(seconds=1)
    due = ledger.due_outbox(project_id="signalharness", now=now)
    assert len(due) == 1
    outbox_id = str(due[0]["outbox_id"])
    idempotency_key = str(due[0]["idempotency_key"])
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        expected = hmac.new(
            b"test-shared-secret",
            request.content,
            hashlib.sha256,
        ).hexdigest()
        assert request.headers["X-SignalHarness-Signature"] == f"sha256={expected}"
        assert request.headers["Idempotency-Key"] == idempotency_key
        assert request.headers["X-SignalHarness-Delivery"] == outbox_id
        status = 503 if len(requests) == 1 else 204
        return httpx.Response(status, request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        first = await service.deliver_due(now=now, client=client)
        assert first[0]["status"] == "retry"
        assert first[0]["attempt_count"] == 1

        second = await service.deliver_due(now=now + timedelta(minutes=3), client=client)
        assert second[0]["status"] == "sent"
        assert second[0]["attempt_count"] == 2

    assert len(requests) == 2
    attempts = ledger.delivery_attempts(outbox_id=outbox_id)
    assert [item["status"] for item in attempts] == ["retry", "sent"]
    assert [item["attempt_number"] for item in attempts] == [1, 2]
    assert ledger.due_outbox(
        project_id="signalharness", now=now + timedelta(days=1)
    ) == []


def test_inbox_rest_read_state_and_delivery_metadata(
    project_root: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import shutil

    from fastapi.testclient import TestClient

    from signal_harness.service import create_app

    monkeypatch.delenv("SIGNALHARNESS_WEBHOOK_URL", raising=False)
    monkeypatch.delenv("SIGNALHARNESS_WEBHOOK_SECRET", raising=False)
    config_dir = tmp_path / "configs"
    shutil.copytree(project_root / "configs", config_dir)
    app = create_app(
        cwd=project_root,
        config_dir=config_dir,
        output_dir=tmp_path / "outputs",
        state_dir=tmp_path / "state",
    )
    with TestClient(app) as client:
        manager = app.state.schedule_manager
        ledger = manager.ledger("signalharness")
        _persist_scan(
            ledger=ledger,
            project_root=project_root,
            scan_id="scan-rest-inbox",
            event=_event(),
            assessment=_assessment(SignalDecision.ACTION_REQUIRED),
        )
        created = manager.notifications("signalharness").ingest_scan(
            scan_id="scan-rest-inbox"
        )
        assert len(created) == 1

        response = client.get("/projects/signalharness/inbox?unread_only=true")
        assert response.status_code == 200
        payload = response.json()
        assert payload["count"] == 1
        assert payload["unread_only"] is True
        assert payload["delivery"] == {
            "signed_webhook_configured": False,
            "destination_key": None,
        }
        inbox_id = payload["items"][0]["inbox_id"]
        assert payload["items"][0]["read_at"] is None

        marked = client.post(f"/projects/signalharness/inbox/{inbox_id}/read")
        assert marked.status_code == 200
        assert marked.json()["read_at"] is not None
        assert client.get(
            "/projects/signalharness/inbox?unread_only=true"
        ).json()["count"] == 0

@pytest.mark.asyncio
async def test_signed_webhook_uses_real_http_transport_on_loopback(
    project_root: Path,
    tmp_path: Path,
) -> None:
    import json
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    received: list[dict[str, object]] = []
    secret = "loopback-shared-secret"

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:  # noqa: N802
            length = int(self.headers.get("Content-Length", "0"))
            body = self.rfile.read(length)
            expected = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
            received.append(
                {
                    "path": self.path,
                    "signature": self.headers.get("X-SignalHarness-Signature"),
                    "idempotency": self.headers.get("Idempotency-Key"),
                    "payload": json.loads(body.decode("utf-8")),
                }
            )
            assert self.headers.get("X-SignalHarness-Signature") == f"sha256={expected}"
            self.send_response(204)
            self.end_headers()

        def log_message(self, format: str, *args: object) -> None:
            del format, args

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        ledger = ChangeLedger(tmp_path / "ledger.sqlite3")
        _persist_scan(
            ledger=ledger,
            project_root=project_root,
            scan_id="scan-loopback-webhook",
            event=_event(),
            assessment=_assessment(SignalDecision.ACTION_REQUIRED),
        )
        webhook = SignedWebhookConfig.create(
            url=f"http://127.0.0.1:{server.server_port}/hook",
            secret=secret,
        )
        service = _service(ledger, project_root, webhook=webhook)
        assert len(service.ingest_scan(scan_id="scan-loopback-webhook")) == 1

        delivered = await service.deliver_due(
            now=datetime.now(timezone.utc) + timedelta(seconds=1)
        )
        assert len(delivered) == 1
        assert delivered[0]["status"] == "sent"
        assert delivered[0]["attempt_count"] == 1
        assert len(received) == 1
        assert received[0]["path"] == "/hook"
        payload = received[0]["payload"]
        assert isinstance(payload, dict)
        assert payload["scan_id"] == "scan-loopback-webhook"
        assert payload["links"]["report"] == "/runs/scan-loopback-webhook/report"
        assert received[0]["idempotency"]
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
