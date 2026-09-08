"""SQLite-backed Event/Change/Scan ledger for the P1 persistence slice."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable
from uuid import uuid4

from signal_harness.projects.preferences import PreferenceInput, apply_preferences
from signal_harness.signal.candidates import candidate_score
from signal_harness.signal.schemas import SignalAssessment, SignalEvent, SourceTask

SCHEMA_VERSION = 4


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
    raw_payload = payload.get("raw_payload")
    if isinstance(raw_payload, dict):
        raw_payload = dict(raw_payload)
        # Scan-local projection metadata must not create a new source EventRevision.
        raw_payload.pop("window_exception", None)
        payload["raw_payload"] = raw_payload
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
            self._ensure_scan_columns(connection)
            connection.execute(
                "INSERT INTO schema_meta(key, value) VALUES('schema_version', ?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (str(SCHEMA_VERSION),),
            )

    @staticmethod
    def _ensure_scan_columns(connection: sqlite3.Connection) -> None:
        columns = {str(row[1]) for row in connection.execute("PRAGMA table_info(scans)")}
        additions = {
            "window_mode": "TEXT NOT NULL DEFAULT 'unbounded'",
            "window_start": "TEXT",
            "window_end": "TEXT",
            "first_use": "INTEGER NOT NULL DEFAULT 0",
            "checkpoint_eligible": "INTEGER NOT NULL DEFAULT 0",
            "coverage_status": "TEXT NOT NULL DEFAULT 'unknown'",
            "profile_revision_id": "TEXT",
        }
        for name, definition in additions.items():
            if name not in columns:
                connection.execute(f"ALTER TABLE scans ADD COLUMN {name} {definition}")

    def active_preferences(self, *, project_id: str) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT preference_id, project_id, scope_type, scope_key, importance, source,
                       instruction, note, created_at, revoked_at, active
                FROM project_preferences
                WHERE project_id=? AND active=1
                ORDER BY created_at ASC, preference_id ASC
                """,
                (project_id,),
            ).fetchall()
        return [self._preference_payload(row) for row in rows]

    def set_preference(
        self, *, project_id: str, preference: PreferenceInput
    ) -> dict[str, Any]:
        preference_id = f"pref-{uuid4().hex[:12]}"
        created_at = _utc_now()
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE project_preferences
                SET active=0, revoked_at=?
                WHERE project_id=? AND scope_type=? AND lower(scope_key)=lower(?) AND active=1
                """,
                (created_at, project_id, preference.scope_type.value, preference.scope_key),
            )
            connection.execute(
                """
                INSERT INTO project_preferences(
                    preference_id, project_id, scope_type, scope_key, importance, source,
                    instruction, note, created_at, active
                ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, 1)
                """,
                (
                    preference_id, project_id, preference.scope_type.value,
                    preference.scope_key, preference.importance.value, preference.source,
                    preference.instruction, preference.note, created_at,
                ),
            )
            row = connection.execute(
                "SELECT * FROM project_preferences WHERE preference_id=?",
                (preference_id,),
            ).fetchone()
        if row is None:
            raise RuntimeError("preference insert could not be resolved")
        return self._preference_payload(row)

    def revoke_preference(self, *, project_id: str, preference_id: str) -> bool:
        with self._connect() as connection:
            cursor = connection.execute(
                """
                UPDATE project_preferences SET active=0, revoked_at=?
                WHERE project_id=? AND preference_id=? AND active=1
                """,
                (_utc_now(), project_id, preference_id),
            )
        return cursor.rowcount > 0

    def ensure_profile_revision(
        self,
        *,
        project_id: str,
        auto_profile: dict[str, Any],
    ) -> dict[str, Any]:
        preferences = self.active_preferences(project_id=project_id)
        effective_profile = apply_preferences(auto_profile, preferences)
        canonical = json.dumps(
            effective_profile, sort_keys=True, separators=(",", ":"), ensure_ascii=False
        )
        profile_hash = _hash_text(canonical)
        with self._connect() as connection:
            existing = connection.execute(
                """
                SELECT * FROM profile_revisions
                WHERE project_id=? AND profile_hash=?
                """,
                (project_id, profile_hash),
            ).fetchone()
            if existing is None:
                revision_id = f"profile-{uuid4().hex[:12]}"
                connection.execute(
                    """
                    INSERT INTO profile_revisions(
                        profile_revision_id, project_id, profile_hash, auto_profile_json,
                        effective_profile_json, evidence_json, unknowns_json, created_at
                    ) VALUES(?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        revision_id, project_id, profile_hash,
                        json.dumps(auto_profile, ensure_ascii=False), canonical,
                        json.dumps(auto_profile.get("evidence", {}), ensure_ascii=False),
                        json.dumps(auto_profile.get("unknowns", []), ensure_ascii=False),
                        _utc_now(),
                    ),
                )
                existing = connection.execute(
                    "SELECT * FROM profile_revisions WHERE profile_revision_id=?",
                    (revision_id,),
                ).fetchone()
        if existing is None:
            raise RuntimeError("profile revision could not be resolved")
        return self._profile_payload(existing, preferences=preferences)

    def latest_profile_revision(self, *, project_id: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT * FROM profile_revisions WHERE project_id=?
                ORDER BY rowid DESC LIMIT 1
                """,
                (project_id,),
            ).fetchone()
        if row is None:
            return None
        return self._profile_payload(
            row, preferences=self.active_preferences(project_id=project_id)
        )

    def scan_profile_revision_id(self, *, scan_id: str) -> str | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT profile_revision_id FROM scans WHERE scan_id=?", (scan_id,)
            ).fetchone()
        if row is None or row["profile_revision_id"] is None:
            return None
        return str(row["profile_revision_id"])

    @staticmethod
    def _preference_payload(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "preference_id": str(row["preference_id"]),
            "project_id": str(row["project_id"]),
            "scope_type": str(row["scope_type"]),
            "scope_key": str(row["scope_key"]),
            "importance": str(row["importance"]),
            "source": str(row["source"]),
            "instruction": str(row["instruction"] or ""),
            "note": str(row["note"] or ""),
            "created_at": str(row["created_at"]),
            "revoked_at": row["revoked_at"],
            "active": bool(row["active"]),
        }

    @staticmethod
    def _profile_payload(
        row: sqlite3.Row, *, preferences: list[dict[str, Any]]
    ) -> dict[str, Any]:
        return {
            "profile_revision_id": str(row["profile_revision_id"]),
            "project_id": str(row["project_id"]),
            "profile_hash": str(row["profile_hash"]),
            "auto_profile": json.loads(str(row["auto_profile_json"])),
            "effective_profile": json.loads(str(row["effective_profile_json"])),
            "evidence": json.loads(str(row["evidence_json"])),
            "unknowns": json.loads(str(row["unknowns_json"])),
            "created_at": str(row["created_at"]),
            "preferences": preferences,
        }

    def begin_scan(
        self,
        *,
        scan_id: str,
        project_id: str,
        collected_count: int,
        deduped_count: int,
        window_mode: str = "unbounded",
        window_start: datetime | None = None,
        window_end: datetime | None = None,
        first_use: bool = False,
        checkpoint_eligible: bool = False,
        profile_revision_id: str | None = None,
    ) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO scans(
                    scan_id, project_id, created_at, status, collected_count, deduped_count,
                    window_mode, window_start, window_end, first_use, checkpoint_eligible,
                    profile_revision_id
                ) VALUES(?, ?, ?, 'running', ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(scan_id) DO UPDATE SET
                    project_id=excluded.project_id,
                    status='running',
                    collected_count=excluded.collected_count,
                    deduped_count=excluded.deduped_count,
                    window_mode=excluded.window_mode,
                    window_start=excluded.window_start,
                    window_end=excluded.window_end,
                    first_use=excluded.first_use,
                    checkpoint_eligible=excluded.checkpoint_eligible,
                    profile_revision_id=excluded.profile_revision_id,
                    error=NULL
                """,
                (
                    scan_id, project_id, _utc_now(), collected_count, deduped_count, window_mode,
                    window_start.isoformat() if window_start else None,
                    window_end.isoformat() if window_end else None,
                    int(first_use), int(checkpoint_eligible), profile_revision_id,
                ),
            )

    def get_interactive_checkpoint(
        self, *, project_id: str, consumer_id: str = "local-owner"
    ) -> datetime | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT checkpoint_at FROM query_checkpoints "
                "WHERE project_id=? AND consumer_id=?",
                (project_id, consumer_id),
            ).fetchone()
        if row is None or row["checkpoint_at"] is None:
            return None
        return datetime.fromisoformat(str(row["checkpoint_at"]))

    def advance_interactive_checkpoint(
        self, *, project_id: str, checkpoint_at: datetime, scan_id: str,
        consumer_id: str = "local-owner"
    ) -> None:
        value = checkpoint_at.astimezone(timezone.utc).isoformat()
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO query_checkpoints(
                    project_id, consumer_id, checkpoint_at, scan_id, updated_at
                ) VALUES(?, ?, ?, ?, ?)
                ON CONFLICT(project_id, consumer_id) DO UPDATE SET
                    checkpoint_at=CASE
                        WHEN excluded.checkpoint_at > query_checkpoints.checkpoint_at
                        THEN excluded.checkpoint_at ELSE query_checkpoints.checkpoint_at END,
                    scan_id=CASE
                        WHEN excluded.checkpoint_at >= query_checkpoints.checkpoint_at
                        THEN excluded.scan_id ELSE query_checkpoints.scan_id END,
                    updated_at=excluded.updated_at
                """,
                (project_id, consumer_id, value, scan_id, _utc_now()),
            )

    def record_source_tasks(self, *, scan_id: str, source_tasks: Iterable[SourceTask]) -> None:
        with self._connect() as connection:
            for task in source_tasks:
                connection.execute(
                    """
                    INSERT INTO scan_sources(
                        scan_id, source_type, source_name, status, coverage_status,
                        pages_fetched, history_limited, output_count, error, diagnostics_json
                    ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(scan_id, source_type, source_name) DO UPDATE SET
                        status=excluded.status,
                        coverage_status=excluded.coverage_status,
                        pages_fetched=excluded.pages_fetched,
                        history_limited=excluded.history_limited,
                        output_count=excluded.output_count,
                        error=excluded.error,
                        diagnostics_json=excluded.diagnostics_json
                    """,
                    (
                        scan_id, task.source_type, task.source_name, task.status,
                        task.coverage_status, task.pages_fetched, int(task.history_limited),
                        task.output_count, task.error, json.dumps(task.diagnostics, ensure_ascii=False),
                    ),
                )

    def source_coverage(self, scan_id: str) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT source_type, source_name, status, coverage_status, pages_fetched,
                       history_limited, output_count, error, diagnostics_json
                FROM scan_sources WHERE scan_id=?
                ORDER BY source_type, source_name
                """,
                (scan_id,),
            ).fetchall()
        return [
            {
                "source_type": str(row["source_type"]),
                "source_name": str(row["source_name"]),
                "status": str(row["status"]),
                "coverage_status": str(row["coverage_status"]),
                "pages_fetched": int(row["pages_fetched"]),
                "history_limited": bool(row["history_limited"]),
                "output_count": int(row["output_count"]),
                "error": row["error"],
                "diagnostics": json.loads(str(row["diagnostics_json"])),
            }
            for row in rows
        ]

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
        coverage_status: str = "complete",
    ) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE scans
                SET status='success', completed_at=?, analyzed_count=?, relevant_count=?, coverage_status=?, error=NULL
                WHERE scan_id=?
                """,
                (_utc_now(), analyzed_count, relevant_count, coverage_status, scan_id),
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

    def observation_state(self, event: SignalEvent) -> str:
        """Return new, revision, or seen for one normalized source observation."""

        event_key = _event_key(event)
        revision_key = _revision_key(event)
        with self._connect() as connection:
            exact = connection.execute(
                "SELECT 1 FROM event_revisions WHERE event_key=? AND revision_key=? LIMIT 1",
                (event_key, revision_key),
            ).fetchone()
            if exact is not None:
                return "seen"
            prior = connection.execute(
                "SELECT 1 FROM event_revisions WHERE event_key=? LIMIT 1",
                (event_key,),
            ).fetchone()
        return "revision" if prior is not None else "new"

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

CREATE TABLE IF NOT EXISTS scan_sources(
    scan_id TEXT NOT NULL REFERENCES scans(scan_id) ON DELETE CASCADE,
    source_type TEXT NOT NULL,
    source_name TEXT NOT NULL,
    status TEXT NOT NULL,
    coverage_status TEXT NOT NULL DEFAULT 'unknown',
    pages_fetched INTEGER NOT NULL DEFAULT 0,
    history_limited INTEGER NOT NULL DEFAULT 0,
    output_count INTEGER NOT NULL DEFAULT 0,
    error TEXT,
    diagnostics_json TEXT NOT NULL DEFAULT '[]',
    PRIMARY KEY(scan_id, source_type, source_name)
);

CREATE TABLE IF NOT EXISTS query_checkpoints(
    project_id TEXT NOT NULL,
    consumer_id TEXT NOT NULL,
    checkpoint_at TEXT NOT NULL,
    scan_id TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY(project_id, consumer_id)
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

CREATE TABLE IF NOT EXISTS profile_revisions(
    profile_revision_id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    profile_hash TEXT NOT NULL,
    auto_profile_json TEXT NOT NULL,
    effective_profile_json TEXT NOT NULL,
    evidence_json TEXT NOT NULL DEFAULT '{}',
    unknowns_json TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL,
    UNIQUE(project_id, profile_hash)
);

CREATE INDEX IF NOT EXISTS idx_profile_revisions_project
ON profile_revisions(project_id, created_at);

CREATE TABLE IF NOT EXISTS project_preferences(
    preference_id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    scope_type TEXT NOT NULL,
    scope_key TEXT NOT NULL,
    importance TEXT NOT NULL,
    source TEXT NOT NULL,
    instruction TEXT NOT NULL DEFAULT '',
    note TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    revoked_at TEXT,
    active INTEGER NOT NULL DEFAULT 1
);

CREATE INDEX IF NOT EXISTS idx_project_preferences_active
ON project_preferences(project_id, active, scope_type, scope_key);
"""
