"""SQLite-backed Event/Change/Scan ledger for the P1 persistence slice."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from signal_harness.signal.candidates import candidate_score
from signal_harness.signal.schemas import SignalAssessment, SignalEvent

SCHEMA_VERSION = 1


@dataclass(frozen=True)
class LedgerChangePage:
    """Stable page of Changes projected from one frozen Scan."""

    items: list[dict[str, Any]]
    count: int
    offset: int
    limit: int

    @property
    def has_more(self) -> bool:
        return self.offset + len(self.items) < self.count


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _hash_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _event_key(event: SignalEvent) -> str:
    """Stable source identity, separate from mutable event content."""

    native_id = str(
        event.raw_payload.get("id")
        or event.raw_payload.get("node_id")
        or event.raw_payload.get("guid")
        or event.url
        or event.event_id
    ).strip()
    canonical = "|".join(
        (event.source_type.lower(), event.source_name.lower(), native_id.lower())
    )
    return _hash_text(canonical)


def _revision_key(event: SignalEvent) -> str:
    payload = event.model_dump(mode="json", exclude={"collected_at"})
    return _hash_text(json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False))


def _change_id(event_key: str) -> str:
    return f"chg-{event_key[:24]}"


class ChangeLedger:
    """Own durable Event revisions and frozen Scan projections."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA journal_mode = WAL")
        return connection

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.executescript(_SCHEMA)
            connection.execute(
                "INSERT OR IGNORE INTO schema_meta(key, value) VALUES('schema_version', ?)",
                (str(SCHEMA_VERSION),),
            )

    def begin_scan(
        self,
        *,
        scan_id: str,
        project_id: str,
        collected_count: int,
        deduped_count: int,
    ) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO scans(
                    scan_id, project_id, created_at, status, collected_count, deduped_count
                ) VALUES(?, ?, ?, 'running', ?, ?)
                ON CONFLICT(scan_id) DO UPDATE SET
                    project_id=excluded.project_id,
                    status='running',
                    collected_count=excluded.collected_count,
                    deduped_count=excluded.deduped_count,
                    error=NULL
                """,
                (scan_id, project_id, _utc_now(), collected_count, deduped_count),
            )

    def persist_observations(
        self,
        events: Iterable[SignalEvent],
    ) -> dict[str, tuple[str, int]]:
        """Persist source revisions before any deep-analysis budget is applied."""
        mapping: dict[str, tuple[str, int]] = {}
        with self._connect() as connection:
            for event in events:
                event_key = _event_key(event)
                revision_key = _revision_key(event)
                change_id = _change_id(event_key)
                payload = json.dumps(event.model_dump(mode="json"), ensure_ascii=False)
                connection.execute(
                    """
                    INSERT OR IGNORE INTO event_revisions(
                        event_key, revision_key, source_type, source_name, source_event_id,
                        url, title, published_at, observed_at, payload_json
                    ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        event_key,
                        revision_key,
                        event.source_type,
                        event.source_name,
                        event.event_id,
                        event.url,
                        event.title,
                        event.published_at.isoformat() if event.published_at else None,
                        event.collected_at.isoformat(),
                        payload,
                    ),
                )
                revision_id_row = connection.execute(
                    "SELECT id FROM event_revisions WHERE event_key=? AND revision_key=?",
                    (event_key, revision_key),
                ).fetchone()
                if revision_id_row is None:
                    raise RuntimeError("event revision insert could not be resolved")
                revision_id = int(revision_id_row["id"])
                mapping[event.event_id] = (change_id, revision_id)
                connection.execute(
                    """
                    INSERT INTO changes(change_id, canonical_key, title, created_at, updated_at)
                    VALUES(?, ?, ?, ?, ?)
                    ON CONFLICT(change_id) DO UPDATE SET
                        title=excluded.title,
                        updated_at=excluded.updated_at
                    """,
                    (change_id, event_key, event.title, _utc_now(), _utc_now()),
                )
                connection.execute(
                    """
                    INSERT OR IGNORE INTO change_events(change_id, event_revision_id)
                    VALUES(?, ?)
                    """,
                    (change_id, revision_id),
                )
        return mapping

    def freeze_scan_changes(
        self,
        *,
        scan_id: str,
        project_id: str,
        events: list[SignalEvent],
        event_change_ids: dict[str, tuple[str, int]],
        project_profile: dict[str, Any],
        policy: dict[str, Any],
        analyzed_event_ids: set[str],
        assessments: Iterable[SignalAssessment] = (),
    ) -> None:
        """Freeze every pre-funnel candidate into one Scan with cheap ranking metadata."""

        assessment_by_id = {item.event_id: item for item in assessments}
        ranked = sorted(
            (
                (
                    candidate_score(event, project_profile, policy),
                    event,
                    event_change_ids[event.event_id][0],
                    event_change_ids[event.event_id][1],
                )
                for event in events
            ),
            key=lambda item: (
                -item[0],
                -(item[1].published_at.timestamp() if item[1].published_at else 0.0),
                item[1].event_id,
            ),
        )
        with self._connect() as connection:
            for rank, (basic_score, event, change_id, event_revision_id) in enumerate(
                ranked, start=1
            ):
                assessment = assessment_by_id.get(event.event_id)
                assessment_json = (
                    json.dumps(assessment.model_dump(mode="json"), ensure_ascii=False)
                    if assessment is not None
                    else None
                )
                selected = 1 if event.event_id in analyzed_event_ids else 0
                connection.execute(
                    """
                    INSERT INTO scan_changes(
                        scan_id, change_id, event_revision_id, rank, basic_relevance_score,
                        selected_for_analysis, assessment_json
                    ) VALUES(?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(scan_id, change_id) DO UPDATE SET
                        rank=excluded.rank,
                        basic_relevance_score=excluded.basic_relevance_score,
                        selected_for_analysis=excluded.selected_for_analysis,
                        assessment_json=excluded.assessment_json
                    """,
                    (
                        scan_id, change_id, event_revision_id, rank, basic_score, selected,
                        assessment_json,
                    ),
                )
                connection.execute(
                    """
                    INSERT INTO project_impacts(
                        project_id, change_id, scan_id, basic_relevance_score,
                        analyzed, assessment_json, updated_at
                    ) VALUES(?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(project_id, change_id, scan_id) DO UPDATE SET
                        basic_relevance_score=excluded.basic_relevance_score,
                        analyzed=excluded.analyzed,
                        assessment_json=excluded.assessment_json,
                        updated_at=excluded.updated_at
                    """,
                    (
                        project_id,
                        change_id,
                        scan_id,
                        basic_score,
                        selected,
                        assessment_json,
                        _utc_now(),
                    ),
                )

    def complete_scan(
        self,
        *,
        scan_id: str,
        analyzed_count: int,
        relevant_count: int,
    ) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE scans
                SET status='success', completed_at=?, analyzed_count=?, relevant_count=?, error=NULL
                WHERE scan_id=?
                """,
                (_utc_now(), analyzed_count, relevant_count, scan_id),
            )

    def fail_scan(self, *, scan_id: str, error: str) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE scans
                SET status='error', completed_at=?, error=?
                WHERE scan_id=?
                """,
                (_utc_now(), error[:1000], scan_id),
            )

    def list_scan_changes(self, scan_id: str, *, offset: int = 0, limit: int = 100) -> LedgerChangePage:
        if offset < 0:
            raise ValueError("offset must be non-negative")
        if limit < 1:
            raise ValueError("limit must be positive")
        with self._connect() as connection:
            count_row = connection.execute(
                "SELECT COUNT(*) AS count FROM scan_changes WHERE scan_id=?",
                (scan_id,),
            ).fetchone()
            total = int(count_row["count"]) if count_row is not None else 0
            rows = connection.execute(
                """
                SELECT
                    sc.change_id,
                    sc.rank,
                    sc.basic_relevance_score,
                    sc.selected_for_analysis,
                    sc.assessment_json,
                    c.title,
                    er.source_type,
                    er.source_name,
                    er.url,
                    er.published_at,
                    er.payload_json
                FROM scan_changes sc
                JOIN changes c ON c.change_id=sc.change_id
                JOIN event_revisions er ON er.id=sc.event_revision_id
                WHERE sc.scan_id=?
                ORDER BY sc.rank ASC, sc.change_id ASC
                LIMIT ? OFFSET ?
                """,
                (scan_id, limit, offset),
            ).fetchall()
            items: list[dict[str, Any]] = []
            for row in rows:
                event_payload = json.loads(str(row["payload_json"]))
                assessment_payload = (
                    json.loads(str(row["assessment_json"]))
                    if row["assessment_json"] is not None
                    else None
                )
                items.append(
                    {
                        "change_id": str(row["change_id"]),
                        "rank": int(row["rank"]),
                        "basic_relevance_score": float(row["basic_relevance_score"]),
                        "selected_for_analysis": bool(row["selected_for_analysis"]),
                        "title": str(row["title"]),
                        "source_type": str(row["source_type"]),
                        "source_name": str(row["source_name"]),
                        "url": str(row["url"] or ""),
                        "published_at": row["published_at"],
                        "event": event_payload,
                        "assessment": assessment_payload,
                    }
                )
        return LedgerChangePage(items=items, count=total, offset=offset, limit=limit)

    def revision_count(self, *, event_id: str) -> int:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT COUNT(*) AS count FROM event_revisions WHERE source_event_id=?",
                (event_id,),
            ).fetchone()
            return int(row["count"]) if row is not None else 0


_SCHEMA = """
CREATE TABLE IF NOT EXISTS schema_meta(
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS event_revisions(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_key TEXT NOT NULL,
    revision_key TEXT NOT NULL,
    source_type TEXT NOT NULL,
    source_name TEXT NOT NULL,
    source_event_id TEXT NOT NULL,
    url TEXT NOT NULL DEFAULT '',
    title TEXT NOT NULL,
    published_at TEXT,
    observed_at TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    UNIQUE(event_key, revision_key)
);

CREATE INDEX IF NOT EXISTS idx_event_revisions_event_key
ON event_revisions(event_key, id);

CREATE INDEX IF NOT EXISTS idx_event_revisions_source_event_id
ON event_revisions(source_event_id, id);

CREATE TABLE IF NOT EXISTS changes(
    change_id TEXT PRIMARY KEY,
    canonical_key TEXT NOT NULL UNIQUE,
    title TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS change_events(
    change_id TEXT NOT NULL REFERENCES changes(change_id) ON DELETE CASCADE,
    event_revision_id INTEGER NOT NULL REFERENCES event_revisions(id) ON DELETE CASCADE,
    PRIMARY KEY(change_id, event_revision_id)
);

CREATE TABLE IF NOT EXISTS scans(
    scan_id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    created_at TEXT NOT NULL,
    completed_at TEXT,
    status TEXT NOT NULL,
    collected_count INTEGER NOT NULL DEFAULT 0,
    deduped_count INTEGER NOT NULL DEFAULT 0,
    analyzed_count INTEGER NOT NULL DEFAULT 0,
    relevant_count INTEGER NOT NULL DEFAULT 0,
    error TEXT
);

CREATE TABLE IF NOT EXISTS scan_changes(
    scan_id TEXT NOT NULL REFERENCES scans(scan_id) ON DELETE CASCADE,
    change_id TEXT NOT NULL REFERENCES changes(change_id) ON DELETE CASCADE,
    event_revision_id INTEGER NOT NULL REFERENCES event_revisions(id) ON DELETE RESTRICT,
    rank INTEGER NOT NULL,
    basic_relevance_score REAL NOT NULL,
    selected_for_analysis INTEGER NOT NULL DEFAULT 0,
    assessment_json TEXT,
    PRIMARY KEY(scan_id, change_id)
);

CREATE INDEX IF NOT EXISTS idx_scan_changes_scan_rank
ON scan_changes(scan_id, rank, change_id);

CREATE TABLE IF NOT EXISTS project_impacts(
    project_id TEXT NOT NULL,
    change_id TEXT NOT NULL REFERENCES changes(change_id) ON DELETE CASCADE,
    scan_id TEXT NOT NULL REFERENCES scans(scan_id) ON DELETE CASCADE,
    basic_relevance_score REAL NOT NULL,
    analyzed INTEGER NOT NULL DEFAULT 0,
    assessment_json TEXT,
    updated_at TEXT NOT NULL,
    PRIMARY KEY(project_id, change_id, scan_id)
);
"""
