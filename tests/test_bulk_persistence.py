from __future__ import annotations

from pathlib import Path

from app.domain.enums import Severity
from app.domain.models import Candidate
from app.persistence.database import Database
from app.persistence.repositories import FindingRepository, ScanRepository


def _cand(rid: str, *, rule: str = "missing_required", field: str = "Name", conf: float = 0.9) -> Candidate:
    return Candidate(
        object_name="Account", record_id=rid, record_name=rid, finding_type=rule,
        field_name=field, severity=Severity.HIGH, confidence=conf, priority=0.9,
        title="t", evidence={"why": "x"},
    )


def _db(tmp_path: Path) -> tuple[Database, ScanRepository, FindingRepository]:
    db = Database(tmp_path / "t.db")
    db.setup()
    scans, findings = ScanRepository(), FindingRepository()
    with db.connect() as conn:
        scans.insert_running(conn, scan_id="s1", source_name="t", object_name="Account",
                             created_at="2026-01-01T00:00:00+00:00", row_count=0, source_hash="h", fields=["Id", "Name"])
    return db, scans, findings


def test_bulk_insert_and_idempotent_rescan(tmp_path):
    db, _, findings = _db(tmp_path)
    cands = [_cand(f"00Q{i:03d}") for i in range(5)]
    with db.connect() as conn:
        findings.upsert_many(conn, scan_id="s1", candidates=cands, source_hash="h", scan_created_at="t0")
    with db.connect() as conn:
        assert findings.count_for_scan(conn, "s1") == 5
        assert findings.status_counts(conn) == {"open": 5}
    # Re-scan: same fingerprints -> still 5 (idempotent upsert, not duplicated)
    with db.connect() as conn:
        findings.upsert_many(conn, scan_id="s1", candidates=cands, source_hash="h", scan_created_at="t1")
        assert findings.count_for_scan(conn, "s1") == 5


def test_bulk_reopen_resolved(tmp_path):
    db, _, findings = _db(tmp_path)
    cands = [_cand(f"00Q{i:03d}") for i in range(3)]
    with db.connect() as conn:
        findings.upsert_many(conn, scan_id="s1", candidates=cands, source_hash="h", scan_created_at="t0")
        rows = findings.list(conn, scan_id="s1")
        findings.set_status(conn, rows[0]["id"], "resolved")
    with db.connect() as conn:
        findings.upsert_many(conn, scan_id="s1", candidates=cands, source_hash="h", scan_created_at="t1")
        counts = findings.status_counts(conn)
    assert counts.get("reopened") == 1
    assert counts.get("open") == 2


def test_bulk_dedupes_same_fingerprint_within_batch(tmp_path):
    db, _, findings = _db(tmp_path)
    dup = [_cand("00Q001"), _cand("00Q001")]  # identical fingerprint
    with db.connect() as conn:
        findings.upsert_many(conn, scan_id="s1", candidates=dup, source_hash="h", scan_created_at="t0")
        assert findings.count_for_scan(conn, "s1") == 1


def test_bulk_handles_batch_over_chunk_limit(tmp_path):
    """>900 distinct candidates exercises the chunked existence query."""
    db, _, findings = _db(tmp_path)
    cands = [_cand(f"00Q{i:04d}") for i in range(1000)]
    with db.connect() as conn:
        findings.upsert_many(conn, scan_id="s1", candidates=cands, source_hash="h", scan_created_at="t0")
        assert findings.count_for_scan(conn, "s1") == 1000
    # idempotent on a large re-scan too
    with db.connect() as conn:
        findings.upsert_many(conn, scan_id="s1", candidates=cands, source_hash="h", scan_created_at="t1")
        assert findings.count_for_scan(conn, "s1") == 1000
