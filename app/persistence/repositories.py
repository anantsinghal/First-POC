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

    def get(self, conn: sqlite3.Connection, scan_id: str) -> dict[str, Any] | None:
        """Return serialised manifest or None."""
        row = conn.execute("SELECT * FROM scan_manifests WHERE id=?", (scan_id,)).fetchone()
        return serialise_row(row) if row else None

    def list(self, conn: sqlite3.Connection, limit: int = 50) -> list[dict[str, Any]]:
        """List recent scans, serialised."""
        rows = conn.execute(
            "SELECT * FROM scan_manifests ORDER BY created_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [serialise_row(row) for row in rows]

    def latest_id(self, conn: sqlite3.Connection) -> str | None:
        """Return most recent scan id or None."""
        row = conn.execute("SELECT id FROM scan_manifests ORDER BY created_at DESC LIMIT 1").fetchone()
        return row["id"] if row else None

    def count(self, conn: sqlite3.Connection) -> int:
        """Total number of scans."""
        row = conn.execute("SELECT COUNT(*) FROM scan_manifests").fetchone()
        return row[0] if row else 0


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
        """Idempotent upsert: existing -> update (reopen if was resolved); else insert new. severity as .value."""
        existing = conn.execute(
            "SELECT id, status FROM findings WHERE fingerprint = ?",
            (candidate.fingerprint,),
        ).fetchone()
        if existing:
            conn.execute(
                """UPDATE findings SET scan_id=?, record_name=?, severity=?, confidence=?, priority=?, title=?, evidence_json=?, source_hash=?, last_seen_at=?, status=? WHERE id=?""",
                (
                    scan_id,
                    candidate.record_name,
                    candidate.severity.value,
                    candidate.confidence,
                    candidate.priority,
                    candidate.title,
                    json.dumps(candidate.evidence),
                    source_hash,
                    now(),
                    "reopened" if existing["status"] == "resolved" else existing["status"],
                    existing["id"],
                ),
            )
        else:
            conn.execute(
                """INSERT INTO findings (id, fingerprint, scan_id, object_name, record_id, record_name, finding_type, field_name, severity, confidence, priority, status, title, evidence_json, source_hash, created_at, last_seen_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    str(uuid4()),
                    candidate.fingerprint,
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
                ),
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
            f"SELECT * FROM findings {where} ORDER BY priority DESC, last_seen_at DESC",
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

    def count_open(self, conn: sqlite3.Connection) -> int:
        """Open or reopened findings count."""
        row = conn.execute("SELECT COUNT(*) FROM findings WHERE status IN ('open','reopened')").fetchone()
        return row[0] if row else 0

    def set_status(self, conn: sqlite3.Connection, finding_id: str, status: str) -> None:
        """Update status of a finding."""
        conn.execute("UPDATE findings SET status=? WHERE id=?", (status, finding_id))


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
            "SELECT * FROM feedback WHERE finding_id=? ORDER BY created_at DESC",
            (finding_id,),
        ).fetchall()
        return [dict(row) for row in rows]
