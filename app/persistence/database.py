from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterator
from uuid import uuid4


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
        """Context manager: mkdir parents, connect, enable WAL + busy_timeout, row_factory=Row, yield, commit, close.

        WAL enables concurrent readers during writer checkpoints; busy_timeout replaces instant
        'database is locked' errors with a bounded (5s) wait. check_same_thread=False is required
        because scan work is offloaded to worker threads (see app/api/offload.py) that reuse this
        same connection object; callers remain responsible for not touching it concurrently from
        two threads at once (all offloaded calls are awaited sequentially).
        """
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.db_path, check_same_thread=False)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA busy_timeout=5000")
        connection.execute("PRAGMA synchronous=NORMAL")
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
            CREATE TABLE IF NOT EXISTS sync_state (
              object_name TEXT PRIMARY KEY, last_watermark TEXT NOT NULL, updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS record_ledger (
              object_name TEXT NOT NULL, record_id TEXT NOT NULL,
              first_seen_at TEXT NOT NULL, last_seen_at TEXT NOT NULL,
              content_hash TEXT NOT NULL, is_deleted INTEGER NOT NULL DEFAULT 0,
              PRIMARY KEY (object_name, record_id)
            );
            CREATE TABLE IF NOT EXISTS learned_models (
              id TEXT PRIMARY KEY, scope TEXT NOT NULL, feature_version INTEGER NOT NULL,
              model_json TEXT NOT NULL, n_labels INTEGER NOT NULL, metrics_json TEXT NOT NULL,
              trained_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS finding_scan_index ON findings(scan_id);
            CREATE INDEX IF NOT EXISTS finding_status_index ON findings(status);
            """)
            self._migrate_source_identity(conn)

    def _migrate_source_identity(self, conn: sqlite3.Connection) -> None:
        """v1 -> v2 schema migration: add source_key to findings/scan_manifests (simple ALTER),
        rebuild sync_state/record_ledger with source_key folded into their PRIMARY KEY (table
        rebuild, since SQLite can't ALTER a primary key), backfill source_key from source_name,
        and recompute every finding's fingerprint to include the source dimension so identity no
        longer collides across two different sources that happen to share an object/record id.

        Guarded by PRAGMA user_version so it runs at most once per database file; runs unconditionally
        the first time (including on a brand-new empty database, where every step is a fast no-op)
        so every database ends up on the same v2 shape regardless of when it was created.
        """
        from .source_key import source_key_from_name
        from ..domain.models import stable_hash

        user_version = conn.execute("PRAGMA user_version").fetchone()[0]
        if user_version >= 2:
            return

        def _has_column(table: str, column: str) -> bool:
            return any(row[1] == column for row in conn.execute(f"PRAGMA table_info({table})").fetchall())

        if not _has_column("findings", "source_key"):
            conn.execute("ALTER TABLE findings ADD COLUMN source_key TEXT NOT NULL DEFAULT ''")
        if not _has_column("scan_manifests", "source_key"):
            conn.execute("ALTER TABLE scan_manifests ADD COLUMN source_key TEXT NOT NULL DEFAULT ''")

        if not _has_column("sync_state", "source_key"):
            conn.executescript("""
                CREATE TABLE sync_state__v2 (
                  source_key TEXT NOT NULL DEFAULT '', object_name TEXT NOT NULL,
                  last_watermark TEXT NOT NULL, updated_at TEXT NOT NULL,
                  PRIMARY KEY (source_key, object_name)
                );
                INSERT INTO sync_state__v2 (source_key, object_name, last_watermark, updated_at)
                    SELECT '', object_name, last_watermark, updated_at FROM sync_state;
                DROP TABLE sync_state;
                ALTER TABLE sync_state__v2 RENAME TO sync_state;
            """)

        if not _has_column("record_ledger", "source_key"):
            conn.executescript("""
                CREATE TABLE record_ledger__v2 (
                  source_key TEXT NOT NULL DEFAULT '', object_name TEXT NOT NULL, record_id TEXT NOT NULL,
                  first_seen_at TEXT NOT NULL, last_seen_at TEXT NOT NULL,
                  content_hash TEXT NOT NULL, is_deleted INTEGER NOT NULL DEFAULT 0,
                  PRIMARY KEY (source_key, object_name, record_id)
                );
                INSERT INTO record_ledger__v2 (
                    source_key, object_name, record_id, first_seen_at, last_seen_at, content_hash, is_deleted
                ) SELECT '', object_name, record_id, first_seen_at, last_seen_at, content_hash, is_deleted
                    FROM record_ledger;
                DROP TABLE record_ledger;
                ALTER TABLE record_ledger__v2 RENAME TO record_ledger;
            """)

        # Backfill scan_manifests.source_key from source_name.
        manifest_rows = conn.execute("SELECT id, source_name FROM scan_manifests").fetchall()
        scan_source_key: dict[str, str] = {
            row["id"]: source_key_from_name(row["source_name"]) for row in manifest_rows
        }
        if scan_source_key:
            conn.executemany(
                "UPDATE scan_manifests SET source_key=? WHERE id=?",
                [(key, scan_id) for scan_id, key in scan_source_key.items()],
            )

        # Backfill findings.source_key from their scan's source_key (orphaned -> 'src:legacy'),
        # then recompute the stored fingerprint to fold the source dimension in. Collisions (two
        # rows landing on the same new fingerprint) keep the row with the latest last_seen_at and
        # delete the other (and its feedback), so the UNIQUE index on fingerprint never trips.
        finding_rows = conn.execute(
            "SELECT id, scan_id, object_name, record_id, finding_type, field_name, fingerprint, last_seen_at "
            "FROM findings"
        ).fetchall()

        owner_by_new_fp: dict[str, sqlite3.Row] = {}
        deletions: list[str] = []
        updates_by_id: dict[str, tuple[str, str]] = {}  # finding_id -> (source_key, new_fingerprint)

        for row in finding_rows:
            key = scan_source_key.get(row["scan_id"], "src:legacy")
            new_fp = stable_hash({
                "source": key,
                "object": row["object_name"],
                "record": row["record_id"],
                "rule": row["finding_type"],
                "field": row["field_name"],
            })
            other = owner_by_new_fp.get(new_fp)
            if other is None:
                owner_by_new_fp[new_fp] = row
                updates_by_id[row["id"]] = (key, new_fp)
            elif row["last_seen_at"] >= other["last_seen_at"]:
                deletions.append(other["id"])
                updates_by_id.pop(other["id"], None)
                owner_by_new_fp[new_fp] = row
                updates_by_id[row["id"]] = (key, new_fp)
            else:
                deletions.append(row["id"])

        if deletions:
            conn.executemany("DELETE FROM feedback WHERE finding_id=?", [(fid,) for fid in deletions])
            conn.executemany("DELETE FROM findings WHERE id=?", [(fid,) for fid in deletions])
        if updates_by_id:
            conn.executemany(
                "UPDATE findings SET source_key=?, fingerprint=? WHERE id=?",
                [(key, new_fp, fid) for fid, (key, new_fp) in updates_by_id.items()],
            )

        if manifest_rows or finding_rows:
            conn.execute(
                """INSERT INTO audit_log (id, ts, event, actor, scan_id, object_name, detail_json)
                   VALUES (?, ?, 'identity_migration', 'system', NULL, NULL, ?)""",
                (
                    str(uuid4()),
                    now(),
                    json.dumps({
                        "scans_migrated": len(manifest_rows),
                        "findings_migrated": len(finding_rows),
                        "collisions_deleted": len(deletions),
                    }),
                ),
            )

        conn.execute("PRAGMA user_version=2")
