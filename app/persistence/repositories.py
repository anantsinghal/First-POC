from __future__ import annotations

import json
import sqlite3
from typing import Any
from uuid import uuid4

from ..domain.models import Candidate, Coverage
from .database import now, serialise_row


class ScanRepository:
    """Repository for scan_manifests. All methods take an open Connection as first arg."""

    def insert_running(
        self,
        conn: sqlite3.Connection,
        *,
        scan_id: str,
        source_name: str,
        object_name: str,
        created_at: str,
        row_count: int,
        source_hash: str,
        fields: list[str],
    ) -> None:
        """Insert running scan manifest (matches baseline persist_scan INSERT)."""
        conn.execute(
            """INSERT INTO scan_manifests (id, source_name, object_name, status, created_at, row_count, source_hash, policy_version, parser_version, fields_json)
               VALUES (?, ?, ?, 'running', ?, ?, ?, 'poc-policy-v1', 'poc-parser-v1', ?)""",
            (scan_id, source_name, object_name, created_at, row_count, source_hash, json.dumps(fields)),
        )

    def complete(
        self,
        conn: sqlite3.Connection,
        *,
        scan_id: str,
        completed_at: str,
        profile: dict[str, Any],
        notes: str,
    ) -> None:
        """Mark scan completed, store profile_json (from Profile.to_dict()) and notes."""
        conn.execute(
            """UPDATE scan_manifests SET status='completed', completed_at=?, profile_json=?, notes=? WHERE id=?""",
            (completed_at, json.dumps(profile), notes, scan_id),
        )

    def set_row_count(self, conn: sqlite3.Connection, scan_id: str, row_count: int) -> None:
        """Update the manifest row_count once the streaming scan finishes counting rows."""
        conn.execute(
            "UPDATE scan_manifests SET row_count=? WHERE id=?",
            (row_count, scan_id),
        )

    def get(self, conn: sqlite3.Connection, scan_id: str) -> dict[str, Any] | None:
        """Return serialised manifest or None."""
        row = conn.execute("SELECT * FROM scan_manifests WHERE id=?", (scan_id,)).fetchone()
        return serialise_row(row) if row else None

    def list(self, conn: sqlite3.Connection, limit: int = 50) -> list[dict[str, Any]]:
        """List recent scans, serialised."""
        rows = conn.execute(
            "SELECT * FROM scan_manifests ORDER BY created_at DESC, rowid DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [serialise_row(row) for row in rows]

    def latest_id(self, conn: sqlite3.Connection) -> str | None:
        """Return most recent scan id or None."""
        row = conn.execute("SELECT id FROM scan_manifests ORDER BY created_at DESC, rowid DESC LIMIT 1").fetchone()
        return row["id"] if row else None

    def previous_scan_id(
        self, conn: sqlite3.Connection, object_name: str, before_created_at: str
    ) -> str | None:
        """Return the most recent completed scan id for the same object before the timestamp."""
        row = conn.execute(
            """SELECT id FROM scan_manifests
               WHERE object_name=? AND created_at<? AND status='completed'
               ORDER BY created_at DESC, rowid DESC
               LIMIT 1""",
            (object_name, before_created_at),
        ).fetchone()
        return row["id"] if row else None

    def count(self, conn: sqlite3.Connection) -> int:
        """Total number of scans."""
        row = conn.execute("SELECT COUNT(*) FROM scan_manifests").fetchone()
        return row[0] if row else 0

    def save_schema(
        self, conn: sqlite3.Connection, *, scan_id: str, object_name: str, schema: dict[str, Any]
    ) -> None:
        """Persist the schema dict (from ObjectSchema.to_dict()) for a scan that supplied metadata."""
        conn.execute(
            "INSERT INTO scan_schemas (scan_id, object_name, schema_json) VALUES (?, ?, ?)",
            (scan_id, object_name, json.dumps(schema)),
        )

    def get_schema(self, conn: sqlite3.Connection, scan_id: str) -> dict | None:
        """Return the parsed schema dict for the scan, or None if absent (e.g. CSV scans)."""
        row = conn.execute(
            "SELECT schema_json FROM scan_schemas WHERE scan_id=?", (scan_id,)
        ).fetchone()
        if row and row["schema_json"]:
            return json.loads(row["schema_json"])
        return None

    def save_quality(
        self, conn: sqlite3.Connection, *, scan_id: str, observed_records: int, flagged_records: int
    ) -> None:
        """Snapshot the scan's observed + distinct-flagged record counts at completion (stable trend basis)."""
        conn.execute(
            "INSERT OR REPLACE INTO scan_quality (scan_id, observed_records, flagged_records) VALUES (?, ?, ?)",
            (scan_id, observed_records, flagged_records),
        )

    def get_quality(self, conn: sqlite3.Connection, scan_id: str) -> dict[str, int] | None:
        """Return the {observed_records, flagged_records} snapshot for a scan, or None."""
        row = conn.execute(
            "SELECT observed_records, flagged_records FROM scan_quality WHERE scan_id=?", (scan_id,)
        ).fetchone()
        return {"observed_records": row[0], "flagged_records": row[1]} if row else None

    def ids_older_than(self, conn: sqlite3.Connection, cutoff_iso: str) -> list[str]:
        """Return list of scan ids where created_at < cutoff_iso (for purge selection)."""
        rows = conn.execute(
            "SELECT id FROM scan_manifests WHERE created_at < ?",
            (cutoff_iso,),
        ).fetchall()
        return [row["id"] for row in rows]

    def purge_scan(self, conn: sqlite3.Connection, scan_id: str) -> None:
        """Delete all per-scan data for a scan (feedback, findings, coverage, schemas, manifest).
        audit_log is intentionally left untouched.
        """
        conn.execute(
            "DELETE FROM feedback WHERE finding_id IN (SELECT id FROM findings WHERE scan_id=?)",
            (scan_id,),
        )
        conn.execute("DELETE FROM findings WHERE scan_id=?", (scan_id,))
        conn.execute("DELETE FROM coverage WHERE scan_id=?", (scan_id,))
        conn.execute("DELETE FROM scan_schemas WHERE scan_id=?", (scan_id,))
        conn.execute("DELETE FROM scan_quality WHERE scan_id=?", (scan_id,))
        conn.execute("DELETE FROM scan_manifests WHERE id=?", (scan_id,))


class CoverageRepository:
    """Repository for coverage table."""

    def insert(self, conn: sqlite3.Connection, scan_id: str, coverage: Coverage) -> None:
        """Insert coverage using domain object (grade as .value)."""
        conn.execute(
            """INSERT INTO coverage (scan_id, object_name, observed_records, queried_fields_json, missing_fields_json, suppressed_checks_json, grade, reason)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                scan_id,
                coverage.object_name,
                coverage.observed_records,
                json.dumps(coverage.queried_fields),
                json.dumps(coverage.missing_fields),
                json.dumps(coverage.suppressed_checks),
                coverage.grade.value,
                coverage.reason,
            ),
        )

    def get(self, conn: sqlite3.Connection, scan_id: str) -> dict[str, Any] | None:
        """Return serialised coverage or None."""
        row = conn.execute("SELECT * FROM coverage WHERE scan_id=?", (scan_id,)).fetchone()
        return serialise_row(row) if row else None


class FindingRepository:
    """Repository for findings. upsert replicates baseline logic exactly."""

    def upsert(
        self,
        conn: sqlite3.Connection,
        *,
        scan_id: str,
        candidate: Candidate,
        source_hash: str,
        scan_created_at: str,
    ) -> None:
        """Idempotent single-candidate upsert (delegates to the batched path for one code path)."""
        self.upsert_many(
            conn,
            scan_id=scan_id,
            candidates=[candidate],
            source_hash=source_hash,
            scan_created_at=scan_created_at,
        )

    def upsert_many(
        self,
        conn: sqlite3.Connection,
        *,
        scan_id: str,
        candidates: list[Candidate],
        source_hash: str,
        scan_created_at: str,
    ) -> None:
        """Set-based idempotent upsert for many candidates.

        Same semantics as the single upsert (existing fingerprint -> update, reopen if was resolved;
        else insert; severity stored as .value), but uses one batched existence query + executemany
        instead of a SELECT+write per candidate — so persisting millions of findings scales.
        """
        if not candidates:
            return
        # Dedupe by fingerprint within this batch (last occurrence wins, matching the sequential loop).
        by_fingerprint: dict[str, Candidate] = {}
        for candidate in candidates:
            by_fingerprint[candidate.fingerprint] = candidate

        existing: dict[str, tuple[str, str]] = {}
        fingerprints = list(by_fingerprint.keys())
        chunk = 900  # stay under SQLite's bound-parameter limit
        for start in range(0, len(fingerprints), chunk):
            batch = fingerprints[start : start + chunk]
            placeholders = ",".join("?" * len(batch))
            rows = conn.execute(
                f"SELECT id, fingerprint, status FROM findings WHERE fingerprint IN ({placeholders})",
                batch,
            ).fetchall()
            for row in rows:
                existing[row["fingerprint"]] = (row["id"], row["status"])

        timestamp = now()
        updates: list[tuple[Any, ...]] = []
        inserts: list[tuple[Any, ...]] = []
        for fingerprint, candidate in by_fingerprint.items():
            if fingerprint in existing:
                existing_id, existing_status = existing[fingerprint]
                updates.append((
                    scan_id,
                    candidate.record_name,
                    candidate.severity.value,
                    candidate.confidence,
                    candidate.priority,
                    candidate.title,
                    json.dumps(candidate.evidence),
                    source_hash,
                    timestamp,
                    "reopened" if existing_status == "resolved" else existing_status,
                    existing_id,
                ))
            else:
                inserts.append((
                    str(uuid4()),
                    fingerprint,
                    scan_id,
                    candidate.object_name,
                    candidate.record_id,
                    candidate.record_name,
                    candidate.finding_type,
                    candidate.field_name,
                    candidate.severity.value,
                    candidate.confidence,
                    candidate.priority,
                    "open",
                    candidate.title,
                    json.dumps(candidate.evidence),
                    source_hash,
                    scan_created_at,
                    scan_created_at,
                ))
        if updates:
            conn.executemany(
                """UPDATE findings SET scan_id=?, record_name=?, severity=?, confidence=?, priority=?, title=?, evidence_json=?, source_hash=?, last_seen_at=?, status=? WHERE id=?""",
                updates,
            )
        if inserts:
            conn.executemany(
                """INSERT INTO findings (id, fingerprint, scan_id, object_name, record_id, record_name, finding_type, field_name, severity, confidence, priority, status, title, evidence_json, source_hash, created_at, last_seen_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                inserts,
            )

    def list(
        self,
        conn: sqlite3.Connection,
        *,
        scan_id: str | None = None,
        status: str | None = None,
    ) -> list[dict[str, Any]]:
        """List findings (no feedback join; caller adds if needed). Matches baseline WHERE/ORDER."""
        clauses: list[str] = []
        params: list[Any] = []
        if scan_id:
            clauses.append("scan_id=?")
            params.append(scan_id)
        if status:
            clauses.append("status=?")
            params.append(status)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        rows = conn.execute(
            f"SELECT * FROM findings {where} ORDER BY priority DESC, last_seen_at DESC, rowid DESC",
            params,
        ).fetchall()
        return [serialise_row(row) for row in rows]

    def get(self, conn: sqlite3.Connection, finding_id: str) -> dict[str, Any] | None:
        """Return serialised finding or None."""
        row = conn.execute("SELECT * FROM findings WHERE id=?", (finding_id,)).fetchone()
        return serialise_row(row) if row else None

    def exists(self, conn: sqlite3.Connection, finding_id: str) -> bool:
        """Check existence."""
        row = conn.execute("SELECT 1 FROM findings WHERE id=?", (finding_id,)).fetchone()
        return bool(row)

    def count_for_scan(self, conn: sqlite3.Connection, scan_id: str) -> int:
        """Number of findings for a scan."""
        row = conn.execute("SELECT COUNT(*) FROM findings WHERE scan_id=?", (scan_id,)).fetchone()
        return row[0] if row else 0

    def distinct_record_count(self, conn: sqlite3.Connection, scan_id: str) -> int:
        """Count distinct records with findings for a scan."""
        row = conn.execute(
            "SELECT COUNT(DISTINCT record_id) FROM findings WHERE scan_id=?",
            (scan_id,),
        ).fetchone()
        return row[0] if row else 0

    def count_open(self, conn: sqlite3.Connection) -> int:
        """Open or reopened findings count."""
        row = conn.execute("SELECT COUNT(*) FROM findings WHERE status IN ('open','reopened')").fetchone()
        return row[0] if row else 0

    def set_status(self, conn: sqlite3.Connection, finding_id: str, status: str) -> None:
        """Update status of a finding."""
        conn.execute("UPDATE findings SET status=? WHERE id=?", (status, finding_id))

    def resolve_for_records(
        self, conn: sqlite3.Connection, *, object_name: str, record_ids: list[str]
    ) -> int:
        """Resolve open/reopened findings for the given records, returning rows updated."""
        if not record_ids:
            return 0
        updated = 0
        chunk = 900
        for start in range(0, len(record_ids), chunk):
            batch = record_ids[start : start + chunk]
            placeholders = ",".join("?" * len(batch))
            cursor = conn.execute(
                f"""UPDATE findings SET status='resolved'
                    WHERE object_name=? AND record_id IN ({placeholders})
                    AND status IN ('open','reopened')""",
                [object_name, *batch],
            )
            updated += cursor.rowcount
        return updated

    def status_counts(self, conn: sqlite3.Connection) -> dict[str, int]:
        """Counts of findings grouped by status."""
        rows = conn.execute("SELECT status, COUNT(*) FROM findings GROUP BY status").fetchall()
        return {row[0]: row[1] for row in rows}

    def severity_counts(
        self, conn: sqlite3.Connection, *, scan_id: str | None = None
    ) -> dict[str, int]:
        """Counts of findings grouped by severity, optionally scoped to one scan."""
        if scan_id is None:
            rows = conn.execute("SELECT severity, COUNT(*) FROM findings GROUP BY severity").fetchall()
        else:
            rows = conn.execute(
                "SELECT severity, COUNT(*) FROM findings WHERE scan_id=? GROUP BY severity",
                (scan_id,),
            ).fetchall()
        return {row[0]: row[1] for row in rows}

    def type_counts(self, conn: sqlite3.Connection) -> dict[str, int]:
        """Counts of findings grouped by finding_type."""
        rows = conn.execute("SELECT finding_type, COUNT(*) FROM findings GROUP BY finding_type").fetchall()
        return {row[0]: row[1] for row in rows}


class FeedbackRepository:
    """Repository for feedback table."""

    def insert(
        self,
        conn: sqlite3.Connection,
        *,
        finding_id: str,
        label: str,
        note: str,
        reviewer: str,
    ) -> str:
        """Insert feedback, return new id."""
        new_id = str(uuid4())
        conn.execute(
            "INSERT INTO feedback (id, finding_id, label, note, reviewer, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (new_id, finding_id, label, note, reviewer, now()),
        )
        return new_id

    def list_for_finding(self, conn: sqlite3.Connection, finding_id: str) -> list[dict[str, Any]]:
        """List feedback for a finding, newest first (plain dicts)."""
        rows = conn.execute(
            "SELECT * FROM feedback WHERE finding_id=? ORDER BY created_at DESC, rowid DESC",
            (finding_id,),
        ).fetchall()
        return [dict(row) for row in rows]

    def label_counts(self, conn: sqlite3.Connection) -> dict[str, int]:
        """Counts of feedback grouped by label."""
        rows = conn.execute("SELECT label, COUNT(*) FROM feedback GROUP BY label").fetchall()
        return {row[0]: row[1] for row in rows}

    def label_counts_by_type(self, conn: sqlite3.Connection) -> dict[str, dict[str, int]]:
        """Counts of feedback grouped by finding type and label."""
        rows = conn.execute(
            """SELECT f.finding_type, fb.label, COUNT(*)
               FROM feedback fb
               JOIN findings f ON f.id = fb.finding_id
               GROUP BY f.finding_type, fb.label"""
        ).fetchall()
        counts: dict[str, dict[str, int]] = {}
        for row in rows:
            counts.setdefault(row[0], {})[row[1]] = row[2]
        return counts


class AuditRepository:
    """Repository for append-only audit_log. All methods take an open Connection as first arg."""

    def append(
        self,
        conn: sqlite3.Connection,
        *,
        event: str,
        actor: str,
        scan_id: str | None = None,
        object_name: str | None = None,
        detail: dict[str, Any],
    ) -> None:
        """Append an audit entry. detail must contain only counts/grades/labels (no record values/secrets)."""
        conn.execute(
            """INSERT INTO audit_log (id, ts, event, actor, scan_id, object_name, detail_json)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (str(uuid4()), now(), event, actor, scan_id, object_name, json.dumps(detail)),
        )

    def list(self, conn: sqlite3.Connection, limit: int = 100) -> list[dict]:
        """Return recent audit entries (newest first), with detail parsed via serialise_row."""
        rows = conn.execute(
            "SELECT * FROM audit_log ORDER BY ts DESC, rowid DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [serialise_row(row) for row in rows]


class SyncRepository:
    """Repository for incremental sync state and the record-state ledger."""

    def get_watermark(self, conn: sqlite3.Connection, object_name: str) -> str | None:
        """Return the last watermark for the object, or None when absent."""
        row = conn.execute(
            "SELECT last_watermark FROM sync_state WHERE object_name=?",
            (object_name,),
        ).fetchone()
        return row["last_watermark"] if row else None

    def set_watermark(self, conn: sqlite3.Connection, object_name: str, watermark: str) -> None:
        """Upsert the object's watermark and refresh updated_at to now()."""
        conn.execute(
            """INSERT OR REPLACE INTO sync_state (object_name, last_watermark, updated_at)
               VALUES (?, ?, ?)""",
            (object_name, watermark, now()),
        )

    def ledger_upsert_many(
        self,
        conn: sqlite3.Connection,
        *,
        object_name: str,
        entries: list[tuple[str, str]],
        seen_at: str,
    ) -> None:
        """Upsert ledger rows for the records seen in this sync window."""
        if not entries:
            return
        conn.executemany(
            """INSERT INTO record_ledger (
                   object_name, record_id, first_seen_at, last_seen_at, content_hash, is_deleted
               ) VALUES (?, ?, ?, ?, ?, 0)
               ON CONFLICT(object_name, record_id) DO UPDATE SET
                   last_seen_at=excluded.last_seen_at,
                   content_hash=excluded.content_hash,
                   is_deleted=0""",
            [(object_name, record_id, seen_at, seen_at, content_hash) for record_id, content_hash in entries],
        )

    def ledger_active_ids(self, conn: sqlite3.Connection, object_name: str) -> set[str]:
        """Return active record ids for the object."""
        rows = conn.execute(
            "SELECT record_id FROM record_ledger WHERE object_name=? AND is_deleted=0",
            (object_name,),
        ).fetchall()
        return {row["record_id"] for row in rows}

    def ledger_mark_deleted(
        self, conn: sqlite3.Connection, object_name: str, record_ids: list[str]
    ) -> int:
        """Mark the supplied record ids deleted in chunks, returning rows updated."""
        if not record_ids:
            return 0
        updated = 0
        chunk = 900
        for start in range(0, len(record_ids), chunk):
            batch = record_ids[start : start + chunk]
            placeholders = ",".join("?" * len(batch))
            cursor = conn.execute(
                f"""UPDATE record_ledger SET is_deleted=1
                    WHERE object_name=? AND is_deleted=0 AND record_id IN ({placeholders})""",
                [object_name, *batch],
            )
            updated += cursor.rowcount
        return updated
