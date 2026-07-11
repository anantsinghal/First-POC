from __future__ import annotations

import hashlib
import importlib
from pathlib import Path

from fastapi.testclient import TestClient

from app.domain.enums import CoverageGrade, Severity
from app.domain.models import Candidate, Coverage
from app.persistence.database import Database, now
from app.persistence.repositories import (
    AuditRepository,
    CoverageRepository,
    FeedbackRepository,
    FindingRepository,
    ScanRepository,
)
from app.services.retention_service import RetentionService


def _make_sample_candidate() -> Candidate:
    """Minimal valid Candidate."""
    return Candidate(
        object_name="Opportunity",
        record_id="006000000000008AAA",
        record_name="Paper Street",
        finding_type="invalid_stage",
        field_name="StageName",
        severity=Severity.HIGH,
        confidence=0.98,
        priority=0.95,
        title="Unknown Opportunity stage",
        evidence={
            "observed_value": "Not A Stage",
            "allowed_values": ["Prospecting", "Closed Won", "Proposal"],
            "why": "StageName is not in the POC stage allowlist.",
        },
    )


def _make_sample_coverage() -> Coverage:
    """A GOOD coverage object."""
    return Coverage(
        object_name="Opportunity",
        observed_records=11,
        queried_fields=["Id", "Name", "StageName", "Amount", "AccountId", "Contract__c", "Segment__c"],
        missing_fields=[],
        suppressed_checks=[],
        grade=CoverageGrade.GOOD,
        reason="Selected POC fields were present in the supplied file.",
    )


def test_retention_purge_repo_and_service(tmp_path: Path):
    """Purge removes only old scan and its dependents; leaves recent + writes audit. audit_log untouched."""
    db = Database(tmp_path / "retention.db")
    db.setup()

    scan_repo = ScanRepository()
    cov_repo = CoverageRepository()
    find_repo = FindingRepository()
    fb_repo = FeedbackRepository()
    audit_repo = AuditRepository()

    source_hash = hashlib.sha256(b"demo").hexdigest()
    profile = {"record_count": 1, "fields": {}}
    notes = "0 candidate occurrences generated."
    candidate_recent = _make_sample_candidate()
    candidate_old = _make_sample_candidate()
    # make distinct so fingerprint differs (idempotent upsert by fp would otherwise collide)
    candidate_old = Candidate(
        object_name=candidate_old.object_name,
        record_id="006000000000009BBB",  # different record
        record_name=candidate_old.record_name,
        finding_type=candidate_old.finding_type,
        field_name=candidate_old.field_name,
        severity=candidate_old.severity,
        confidence=candidate_old.confidence,
        priority=candidate_old.priority,
        title=candidate_old.title,
        evidence=candidate_old.evidence,
    )
    coverage = _make_sample_coverage()
    schema_dict = {
        "object_name": "Opportunity",
        "fields": [{"name": "Id", "data_type": "id", "required": False}],
    }

    recent_id = "scan-recent"
    old_id = "scan-old"
    recent_created = now()
    old_created = "2020-01-01T00:00:00+00:00"

    # Create recent scan + full dependents
    with db.connect() as conn:
        scan_repo.insert_running(
            conn,
            scan_id=recent_id,
            source_name="recent.csv",
            object_name="Opportunity",
            created_at=recent_created,
            row_count=1,
            source_hash=source_hash,
            fields=["Id", "StageName"],
            source_key="src:recent.csv",
        )
        cov_repo.insert(conn, recent_id, coverage)
        find_repo.upsert(
            conn,
            scan_id=recent_id,
            candidate=candidate_recent,
            source_hash=source_hash,
            scan_created_at=recent_created,
            source_key="src:recent.csv",
        )
        scan_repo.complete(
            conn, scan_id=recent_id, completed_at=recent_created, profile=profile, notes=notes
        )
        f_recent = find_repo.list(conn, scan_id=recent_id)[0]
        fb_repo.insert(
            conn, finding_id=f_recent["id"], label="real_issue", note="", reviewer="tester"
        )
        scan_repo.save_schema(conn, scan_id=recent_id, object_name="Opportunity", schema=schema_dict)

    # Create OLD scan + same dependents
    with db.connect() as conn:
        scan_repo.insert_running(
            conn,
            scan_id=old_id,
            source_name="old.csv",
            object_name="Opportunity",
            created_at=old_created,
            row_count=1,
            source_hash=source_hash,
            fields=["Id", "StageName"],
            source_key="src:old.csv",
        )
        cov_repo.insert(conn, old_id, coverage)
        find_repo.upsert(
            conn,
            scan_id=old_id,
            candidate=candidate_old,
            source_hash=source_hash,
            scan_created_at=old_created,
            source_key="src:old.csv",
        )
        scan_repo.complete(
            conn, scan_id=old_id, completed_at=old_created, profile=profile, notes=notes
        )
        f_old = find_repo.list(conn, scan_id=old_id)[0]
        fb_repo.insert(
            conn, finding_id=f_old["id"], label="false_positive", note="", reviewer="tester"
        )
        scan_repo.save_schema(conn, scan_id=old_id, object_name="Opportunity", schema=schema_dict)

    # Now purge via service
    svc = RetentionService(database=db, scans=scan_repo, audit=audit_repo)
    result = svc.purge(older_than_days=30)

    assert result["purged_scans"] == 1
    assert result["older_than_days"] == 30
    assert "cutoff" in result

    # Verify with fresh connects
    with db.connect() as conn:
        assert scan_repo.get(conn, old_id) is None
        assert cov_repo.get(conn, old_id) is None
        assert find_repo.count_for_scan(conn, old_id) == 0
        # feedback for old gone (via finding delete)
        assert scan_repo.get_schema(conn, old_id) is None

        assert scan_repo.get(conn, recent_id) is not None
        assert cov_repo.get(conn, recent_id) is not None
        assert find_repo.count_for_scan(conn, recent_id) == 1
        assert scan_repo.get_schema(conn, recent_id) is not None

        # audit not touched, and has the purged entry
        audits = audit_repo.list(conn, limit=10)
        purged_audits = [a for a in audits if a.get("event") == "scans_purged"]
        assert len(purged_audits) >= 1
        assert purged_audits[0]["detail"]["purged_scans"] == 1

    # audit_log table still has rows (we appended, and never delete)
    with db.connect() as conn:
        rows = conn.execute("SELECT COUNT(*) FROM audit_log").fetchone()
        assert rows[0] >= 1


def test_retention_purge_noop_for_recent_only(tmp_path: Path):
    """purge(3650) on recent-only data is no-op."""
    db = Database(tmp_path / "retention2.db")
    db.setup()
    scan_repo = ScanRepository()
    cov_repo = CoverageRepository()
    find_repo = FindingRepository()
    audit_repo = AuditRepository()

    source_hash = hashlib.sha256(b"demo").hexdigest()
    recent_id = "scan-recent2"
    created = now()

    with db.connect() as conn:
        scan_repo.insert_running(
            conn,
            scan_id=recent_id,
            source_name="r.csv",
            object_name="Opportunity",
            created_at=created,
            row_count=1,
            source_hash=source_hash,
            fields=["Id"],
            source_key="src:r.csv",
        )
        cov = _make_sample_coverage()
        cov_repo.insert(conn, recent_id, cov)
        scan_repo.complete(conn, scan_id=recent_id, completed_at=created, profile={}, notes="")

    svc = RetentionService(database=db, scans=scan_repo, audit=audit_repo)
    result = svc.purge(older_than_days=3650)
    assert result["purged_scans"] == 0

    with db.connect() as conn:
        assert scan_repo.get(conn, recent_id) is not None


def test_retention_api_purge_and_validation(tmp_path: Path, monkeypatch):
    """API accepts valid, rejects invalid (422), returns 200 with counts."""
    monkeypatch.setenv("SCANNER_DATA_DIR", str(tmp_path))
    import app.main as main

    main = importlib.reload(main)
    with TestClient(main.app) as client:
        # Valid purge call (may purge 0 if no old data, but returns 200)
        r = client.post("/api/admin/purge", json={"older_than_days": 30})
        assert r.status_code == 200
        body = r.json()
        assert "purged_scans" in body
        assert body["older_than_days"] == 30

        # Invalid -> 422
        r_bad = client.post("/api/admin/purge", json={"older_than_days": 0})
        assert r_bad.status_code == 422
