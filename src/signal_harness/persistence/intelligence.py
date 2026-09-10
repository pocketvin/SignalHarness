"""Additive intelligence tables in the existing ledger, never a second state database."""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from collections.abc import Iterator
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from signal_harness.intelligence.contracts import ChangeDigest, EnvironmentReport, ProductChange

INTELLIGENCE_SCHEMA = """
CREATE TABLE IF NOT EXISTS change_revisions(
    revision_id TEXT PRIMARY KEY,
    change_id TEXT NOT NULL REFERENCES changes(change_id),
    digest_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS change_revision_evidence(
    revision_id TEXT NOT NULL REFERENCES change_revisions(revision_id),
    event_revision_id INTEGER NOT NULL REFERENCES event_revisions(id),
    PRIMARY KEY(revision_id, event_revision_id)
);
CREATE TABLE IF NOT EXISTS scan_intelligence(
    scan_id TEXT NOT NULL REFERENCES scans(scan_id),
    change_id TEXT NOT NULL REFERENCES changes(change_id),
    revision_id TEXT NOT NULL REFERENCES change_revisions(revision_id),
    insight_json TEXT,
    relevant INTEGER NOT NULL DEFAULT 0,
    featured INTEGER NOT NULL DEFAULT 0,
    attention TEXT NOT NULL DEFAULT 'normal',
    corpus_role TEXT NOT NULL DEFAULT 'external_environment',
    interpreted INTEGER NOT NULL DEFAULT 0,
    published_at TEXT,
    search_text TEXT NOT NULL DEFAULT '',
    PRIMARY KEY(scan_id, change_id)
);
CREATE INDEX IF NOT EXISTS idx_scan_intelligence_view
ON scan_intelligence(scan_id, relevant, featured, attention, published_at);
CREATE TABLE IF NOT EXISTS insight_cache(
    cache_key TEXT PRIMARY KEY,
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS environment_reports(
    report_id TEXT PRIMARY KEY,
    scan_id TEXT NOT NULL UNIQUE REFERENCES scans(scan_id),
    project_id TEXT NOT NULL,
    window_end TEXT,
    payload_json TEXT NOT NULL,
    audit_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_environment_reports_project
ON environment_reports(project_id, window_end, created_at);
CREATE TABLE IF NOT EXISTS environment_direction_revisions(
    revision_id TEXT PRIMARY KEY,
    direction_id TEXT NOT NULL,
    report_id TEXT NOT NULL REFERENCES environment_reports(report_id),
    project_id TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_direction_history
ON environment_direction_revisions(project_id, direction_id, created_at);
CREATE TABLE IF NOT EXISTS deep_dive_jobs(
    job_id TEXT PRIMARY KEY,
    cache_key TEXT NOT NULL UNIQUE,
    project_id TEXT NOT NULL,
    scan_id TEXT NOT NULL REFERENCES scans(scan_id),
    change_id TEXT NOT NULL,
    revision_id TEXT NOT NULL,
    profile_revision_id TEXT NOT NULL,
    status TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    sequence INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
"""


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)


class IntelligenceRepository:
    """Query/persist intelligence via version-pinned immutable source evidence."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path, timeout=15)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def freeze(self, scan_id: str, digests: list[ChangeDigest]) -> None:
        with self.connect() as db:
            if db.execute(
                "SELECT 1 FROM environment_reports WHERE scan_id=?", (scan_id,)
            ).fetchone():
                raise ValueError("A published environment report cannot be overwritten")
            for item in digests:
                db.execute(
                    "INSERT OR IGNORE INTO change_revisions VALUES(?,?,?,?)",
                    (item.revision_id, item.change_id, item.model_dump_json(), utc_now()),
                )
                for evidence in item.evidence:
                    db.execute(
                        "INSERT OR IGNORE INTO change_revision_evidence VALUES(?,?)",
                        (
                            item.revision_id,
                            evidence.event_revision_id,
                        ),
                    )
                db.execute(
                    "INSERT OR IGNORE INTO scan_intelligence("
                    "scan_id,change_id,revision_id,published_at,corpus_role) VALUES(?,?,?,?,?)",
                    (
                        scan_id,
                        item.change_id,
                        item.revision_id,
                        item.published_at,
                        item.corpus_role,
                    ),
                )

    def save_insight(self, scan_id: str, item: ProductChange) -> None:
        text = " ".join(
            [
                item.title,
                item.entity,
                item.summary,
                item.what_changed,
                item.relation_reason,
                *item.topics,
            ]
        ).casefold()
        with self.connect() as db:
            if db.execute(
                "SELECT 1 FROM environment_reports WHERE scan_id=?", (scan_id,)
            ).fetchone():
                raise ValueError("Historical scan intelligence is frozen")
            result = db.execute(
                "UPDATE scan_intelligence SET insight_json=?,relevant=?,featured=?,attention=?,corpus_role=?,interpreted=?,search_text=? "
                "WHERE scan_id=? AND change_id=? AND revision_id=?",
                (
                    item.model_dump_json(),
                    int(item.relevant),
                    int(item.featured),
                    item.attention,
                    item.corpus_role,
                    int(item.interpretation_status == "ready"),
                    text,
                    scan_id,
                    item.change_id,
                    item.revision_id,
                ),
            )
            if result.rowcount != 1:
                raise ValueError("Insight revision differs from the frozen Scan")

    def get_cached_insight(self, key: str) -> dict[str, Any] | None:
        with self.connect() as db:
            row = db.execute(
                "SELECT payload_json FROM insight_cache WHERE cache_key=?", (key,)
            ).fetchone()
        return json.loads(row[0]) if row else None

    def cache_insight(self, key: str, payload: dict[str, Any]) -> None:
        with self.connect() as db:
            db.execute(
                "INSERT OR REPLACE INTO insight_cache VALUES(?,?,?)",
                (key, dumps(payload), utc_now()),
            )

    def save_report(self, report: EnvironmentReport, audit: dict[str, Any]) -> None:
        with self.connect() as db:
            db.execute(
                "INSERT INTO environment_reports VALUES(?,?,?,?,?,?,?)",
                (
                    report.report_id,
                    report.scan_id,
                    report.project_id,
                    report.window.get("to"),
                    report.model_dump_json(),
                    dumps(audit),
                    report.created_at,
                ),
            )
            for item in report.directions:
                db.execute(
                    "INSERT INTO environment_direction_revisions VALUES(?,?,?,?,?,?)",
                    (
                        item.revision_id,
                        item.direction_id,
                        report.report_id,
                        report.project_id,
                        item.model_dump_json(),
                        report.created_at,
                    ),
                )

    def report(self, scan_id: str) -> dict[str, Any] | None:
        with self.connect() as db:
            row = db.execute(
                "SELECT payload_json FROM environment_reports WHERE scan_id=?", (scan_id,)
            ).fetchone()
        return json.loads(row[0]) if row else None

    def latest_report(self, project_id: str, *, before: str | None = None) -> dict[str, Any] | None:
        clauses, params = ["project_id=?"], [project_id]
        if before:
            clauses.append("window_end<=?")
            params.append(before)
        with self.connect() as db:
            row = db.execute(
                "SELECT payload_json FROM environment_reports WHERE "
                + " AND ".join(clauses)
                + " ORDER BY window_end DESC,created_at DESC LIMIT 1",
                params,
            ).fetchone()
        return json.loads(row[0]) if row else None

    def reports(self, project_id: str) -> list[dict[str, Any]]:
        with self.connect() as db:
            rows = db.execute(
                "SELECT scan_id,created_at,window_end FROM environment_reports WHERE project_id=? "
                "ORDER BY window_end DESC,created_at DESC LIMIT 30",
                (project_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def changes(
        self,
        scan_id: str,
        *,
        view: str = "relevant",
        query: str = "",
        offset: int = 0,
        limit: int = 25,
        ids: list[str] | None = None,
    ) -> dict[str, Any]:
        """Filter, sort and paginate in SQLite, not by loading the entire scan."""
        if (
            not 1 <= limit <= 500
            or offset < 0
            or view not in {"relevant", "all", "featured", "activity", "unavailable"}
        ):
            raise ValueError("Invalid change pagination")
        clauses = ["scan_id=?", "insight_json IS NOT NULL"]
        params: list[Any] = [scan_id]
        if view == "relevant":
            clauses.extend(("relevant=1", "corpus_role='external_environment'"))
        elif view == "all":
            clauses.append("corpus_role='external_environment'")
        elif view == "featured":
            clauses.extend(("featured=1", "corpus_role='external_environment'"))
        elif view == "activity":
            clauses.append("corpus_role='project_activity'")
        elif view == "unavailable":
            clauses.extend(("interpreted=0", "corpus_role='external_environment'"))
        if query.strip():
            escaped = (
                query.strip()
                .casefold()
                .replace("\\", "\\\\")
                .replace("%", "\\%")
                .replace("_", "\\_")
            )
            clauses.append("search_text LIKE ? ESCAPE '\\'")
            params.append(f"%{escaped}%")
        if ids is not None:
            if not ids:
                return {
                    "items": [],
                    "count": 0,
                    "offset": offset,
                    "limit": limit,
                    "has_more": False,
                }
            clauses.append("change_id IN (" + ",".join("?" for _ in ids) + ")")
            params.extend(ids)
        where = " AND ".join(clauses)
        with self.connect() as db:
            count = db.execute(
                "SELECT COUNT(*) FROM scan_intelligence WHERE " + where, params
            ).fetchone()[0]
            rows = db.execute(
                "SELECT insight_json FROM scan_intelligence WHERE "
                + where
                + " ORDER BY featured DESC,CASE attention WHEN 'watch' THEN 0 WHEN 'normal' THEN 1 ELSE 2 END,"
                "published_at DESC,change_id ASC LIMIT ? OFFSET ?",
                [*params, limit, offset],
            ).fetchall()
        return {
            "items": [json.loads(row[0]) for row in rows],
            "count": count,
            "offset": offset,
            "limit": limit,
            "has_more": offset + len(rows) < count,
        }

    def change(self, scan_id: str, change_id: str) -> dict[str, Any] | None:
        with self.connect() as db:
            row = db.execute(
                "SELECT insight_json FROM scan_intelligence WHERE scan_id=? AND change_id=?",
                (scan_id, change_id),
            ).fetchone()
        return json.loads(row[0]) if row and row[0] else None

    def snapshot_events(self, scan_id: str) -> list[dict[str, Any]]:
        with self.connect() as db:
            rows = db.execute(
                "SELECT DISTINCT er.id,er.payload_json FROM scan_intelligence si "
                "JOIN change_revision_evidence cre ON cre.revision_id=si.revision_id "
                "JOIN event_revisions er ON er.id=cre.event_revision_id "
                "WHERE si.scan_id=? ORDER BY er.id",
                (scan_id,),
            ).fetchall()
        return [json.loads(row["payload_json"]) for row in rows]

    def profile_for_scan(self, scan_id: str) -> dict[str, Any]:
        with self.connect() as db:
            row = db.execute(
                "SELECT pr.effective_profile_json,pr.profile_revision_id FROM profile_revisions pr "
                "JOIN scans s ON s.profile_revision_id=pr.profile_revision_id WHERE s.scan_id=?",
                (scan_id,),
            ).fetchone()
        if row is None:
            raise ValueError("Frozen project profile unavailable")
        return {"profile": json.loads(row[0]), "profile_revision_id": row[1]}

    def create_job(self, payload: dict[str, Any]) -> tuple[dict[str, Any], bool]:
        with self.connect() as db:
            inserted = db.execute(
                "INSERT OR IGNORE INTO deep_dive_jobs VALUES(?,?,?,?,?,?,?,?,?,1,?,?)",
                (
                    payload["job_id"],
                    payload["cache_key"],
                    payload["project_id"],
                    payload["scan_id"],
                    payload["change_id"],
                    payload["revision_id"],
                    payload["profile_revision_id"],
                    "queued",
                    dumps(payload),
                    utc_now(),
                    utc_now(),
                ),
            )
            row = db.execute(
                "SELECT * FROM deep_dive_jobs WHERE cache_key=?", (payload["cache_key"],)
            ).fetchone()
        if row is None:
            raise RuntimeError("Deep dive reservation failed")
        return self._job_payload(row), inserted.rowcount == 1

    @staticmethod
    def _job_payload(row: sqlite3.Row) -> dict[str, Any]:
        payload: dict[str, Any] = json.loads(row["payload_json"])
        payload.update(status=row["status"], sequence=row["sequence"], updated_at=row["updated_at"])
        return payload

    def job(self, job_id: str, project_id: str) -> dict[str, Any] | None:
        with self.connect() as db:
            row = db.execute(
                "SELECT * FROM deep_dive_jobs WHERE job_id=? AND project_id=?", (job_id, project_id)
            ).fetchone()
        return self._job_payload(row) if row else None

    def update_job(self, job_id: str, status: str, payload: dict[str, Any]) -> None:
        with self.connect() as db:
            db.execute(
                "UPDATE deep_dive_jobs SET status=?,payload_json=?,sequence=sequence+1,updated_at=? WHERE job_id=?",
                (
                    status,
                    dumps(payload),
                    utc_now(),
                    job_id,
                ),
            )

    def interrupt_jobs(self, project_id: str) -> int:
        with self.connect() as db:
            rows = db.execute(
                "SELECT * FROM deep_dive_jobs WHERE project_id=? AND status IN ('queued','running')",
                (project_id,),
            ).fetchall()
            for row in rows:
                payload = self._job_payload(row)
                payload["error"] = "上次核实被服务重启中断，请点击重试。"
                db.execute(
                    "UPDATE deep_dive_jobs SET status='error',payload_json=?,sequence=sequence+1 WHERE job_id=?",
                    (dumps(payload), row["job_id"]),
                )
        return len(rows)
