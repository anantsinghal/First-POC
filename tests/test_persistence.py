from __future__ import annotations

import hashlib
from pathlib import Path

from app.domain.enums import CoverageGrade, Severity
from app.domain.models import Candidate, Coverage, stable_hash
from app.persistence.database import Database
from app.persistence.repositories import (
    CoverageRepository,
    FeedbackRepository,
    FindingRepository,
    ScanRepository,
)


def _make_sample_candidate() -> Candidate:
    """A minimal valid Candidate (fingerprint computed via property)."""
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
    """A GOOD coverage object matching domain shape."""
    return Coverage(
        object_name="Opportunity",
        observed_records=11,
        queried_fields=["Id", "Name", "StageName", "Amount", "AccountId", "Contract__c", "Segment__c"],
        missing_fields=[],
        suppressed_checks=[],
        grade=CoverageGrade.GOOD,
        reason="Selected POC fields were present in the supplied file.",
    )


def test_persistence_repositories_roundtrip_and_upsert_logic(tmp_path: Path):
    """Exercise full persistence using injected Database + repos (one atomic conn for writes)."""
    db = Database(tmp_path / "t.db")
    db.setup()

    scan_repo = ScanRepository()
    cov_repo = CoverageRepository()
    find_repo = FindingRepository()
    fb_repo = FeedbackRepository()

    scan_id = "scan-123"
    source_hash = hashlib.sha256(b"demo").hexdigest()
    created_at = "2026-06-21T12:00:00+00:00"
    candidate = _make_sample_candidate()
    coverage = _make_sample_coverage()
    profile = {"record_count": 11, "fields": {}}
    notes = "1 candidate occurrences generated."
    source_key = "src:demo.csv"
    stored_fingerprint = stable_hash({
        "source": source_key,
        "object": candidate.object_name,
        "record": candidate.record_id,
        "rule": candidate.finding_type,
        "field": candidate.field_name,
    })

    # Atomic write block (simulates unit-of-work)
    with db.connect() as conn:
        scan_repo.insert_running(
            conn,
            scan_id=scan_id,
            source_name="demo.csv",
            object_name="Opportunity",
            created_at=created_at,
            row_count=11,
            source_hash=source_hash,
            fields=["Id", "Name", "StageName"],
            source_key=source_key,
        )
        cov_repo.insert(conn, scan_id, coverage)
        find_repo.upsert(
            conn,
            scan_id=scan_id,
            candidate=candidate,
            source_hash=source_hash,
            scan_created_at=created_at,
            source_key=source_key,
        )
        scan_repo.complete(
            conn,
            scan_id=scan_id,
            completed_at="2026-06-21T12:01:00+00:00",
            profile=profile,
            notes=notes,
        )

    # Reopen reads
    with db.connect() as conn:
        scan = scan_repo.get(conn, scan_id)
        assert scan is not None
        assert scan["status"] == "completed"
        assert scan["row_count"] == 11

        cov = cov_repo.get(conn, scan_id)
        assert cov is not None
        assert cov["grade"] == "good"  # serialised to str
        assert cov["observed_records"] == 11

        assert find_repo.count_for_scan(conn, scan_id) == 1
        findings = find_repo.list(conn, scan_id=scan_id)
        assert len(findings) == 1
        f = findings[0]
        assert f["severity"] == "high"  # stored as .value plain string
        assert f["evidence"] == candidate.evidence  # round-tripped via serialise_row
        # Stored fingerprint folds in source_key (source-scoped identity); the domain
        # Candidate.fingerprint property itself stays source-blind (engine doesn't know sources).
        assert f["fingerprint"] == stored_fingerprint
        assert f["fingerprint"] != candidate.fingerprint
        assert f["status"] == "open"

    # Idempotency: upsert same candidate twice -> still 1 row
    with db.connect() as conn:
        find_repo.upsert(
            conn,
            scan_id=scan_id,
            candidate=candidate,
            source_hash=source_hash,
            scan_created_at=created_at,
            source_key=source_key,
        )
    with db.connect() as conn:
        assert find_repo.count_for_scan(conn, scan_id) == 1

    # Reopen logic
    with db.connect() as conn:
        # find the finding id
        f = find_repo.list(conn, scan_id=scan_id)[0]
        find_repo.set_status(conn, f["id"], "resolved")
    with db.connect() as conn:
        find_repo.upsert(
            conn,
            scan_id=scan_id,
            candidate=candidate,
            source_hash=source_hash,
            scan_created_at=created_at,
            source_key=source_key,
        )
        f2 = find_repo.list(conn, scan_id=scan_id)[0]
        assert f2["status"] == "reopened"

    # Feedback + set_status
    with db.connect() as conn:
        f = find_repo.list(conn, scan_id=scan_id)[0]
        fb_id = fb_repo.insert(
            conn,
            finding_id=f["id"],
            label="false_positive",
            note="test note",
            reviewer="tester",
        )
        assert fb_id
        fbs = fb_repo.list_for_finding(conn, f["id"])
        assert len(fbs) == 1
        assert fbs[0]["label"] == "false_positive"

        find_repo.set_status(conn, f["id"], "suppressed_until")
        f3 = find_repo.get(conn, f["id"])
        assert f3 is not None
        assert f3["status"] == "suppressed_until"

    # Misc repo methods
    with db.connect() as conn:
        assert scan_repo.count(conn) >= 1
        assert scan_repo.latest_id(conn) == scan_id
        assert find_repo.exists(conn, f["id"])
        assert find_repo.count_open(conn) >= 0
