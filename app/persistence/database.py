from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterator


def now() -> str:
    """Return current UTC ISO timestamp string (matches baseline)."""
    return datetime.now(UTC).isoformat()


def serialise_row(row: sqlite3.Row) -> dict[str, Any]:
    """Deserialize _json columns back to dicts (exact baseline logic)."""
    item = dict(row)
    for key in (
        "fields_json",
        "profile_json",
        "queried_fields_json",
        "missing_fields_json",
        "suppressed_checks_json",
        "evidence_json",
        "schema_json",
        "detail_json",
    ):
        if key in item and item[key] is not None:
            item[key.removesuffix("_json")] = json.loads(item.pop(key))
    return item


class Database:
    """Injected DB wrapper. No env vars; path provided by caller. Encapsulates connect + schema."""

    def __init__(self, db_path: Path) -> None:
        """Store the db file path (parent dir will be created on connect)."""
        self.db_path = db_path

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        """Context manager: mkdir parents, connect, row_factory=Row, yield, commit, close."""
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.db_path)
        connection.row_factory = sqlite3.Row
        try:
            yield connection
            connection.commit()
        finally:
            connection.close()

    def setup(self) -> None:
        """Create tables/indexes using EXACT baseline schema (unchanged column names)."""
        with self.connect() as conn:
            conn.executescript("""
            CREATE TABLE IF NOT EXISTS scan_manifests (
              id TEXT PRIMARY KEY, source_name TEXT NOT NULL, object_name TEXT NOT NULL,
              status TEXT NOT NULL, created_at TEXT NOT NULL, completed_at TEXT,
              row_count INTEGER NOT NULL DEFAULT 0, source_hash TEXT NOT NULL,
              policy_version TEXT NOT NULL, parser_version TEXT NOT NULL,
              fields_json TEXT NOT NULL, profile_json TEXT, notes TEXT
            );
            CREATE TABLE IF NOT EXISTS coverage (
              scan_id TEXT PRIMARY KEY REFERENCES scan_manifests(id), object_name TEXT NOT NULL,
              observed_records INTEGER NOT NULL, queried_fields_json TEXT NOT NULL,
              missing_fields_json TEXT NOT NULL, suppressed_checks_json TEXT NOT NULL,
              grade TEXT NOT NULL, reason TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS findings (
              id TEXT PRIMARY KEY, fingerprint TEXT UNIQUE NOT NULL, scan_id TEXT NOT NULL,
              object_name TEXT NOT NULL, record_id TEXT NOT NULL, record_name TEXT,
              finding_type TEXT NOT NULL, field_name TEXT, severity TEXT NOT NULL,
              confidence REAL NOT NULL, priority REAL NOT NULL, status TEXT NOT NULL,
              title TEXT NOT NULL, evidence_json TEXT NOT NULL, source_hash TEXT NOT NULL,
              created_at TEXT NOT NULL, last_seen_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS feedback (
              id TEXT PRIMARY KEY, finding_id TEXT NOT NULL REFERENCES findings(id),
              label TEXT NOT NULL, note TEXT, reviewer TEXT NOT NULL, created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS scan_schemas (
              scan_id TEXT PRIMARY KEY REFERENCES scan_manifests(id), object_name TEXT NOT NULL,
              schema_json TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS audit_log (
              id TEXT PRIMARY KEY, ts TEXT NOT NULL, event TEXT NOT NULL, actor TEXT NOT NULL,
              scan_id TEXT, object_name TEXT, detail_json TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS scan_quality (
              scan_id TEXT PRIMARY KEY REFERENCES scan_manifests(id),
              observed_records INTEGER NOT NULL, flagged_records INTEGER NOT NULL
            );
            CREATE INDEX IF NOT EXISTS finding_scan_index ON findings(scan_id);
            CREATE INDEX IF NOT EXISTS finding_status_index ON findings(status);
            """)
