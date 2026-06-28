from __future__ import annotations

import importlib
from pathlib import Path

from fastapi.testclient import TestClient

from app.persistence.database import Database
from app.persistence.repositories import AuditRepository


def test_audit_repository_roundtrip(tmp_path: Path):
    """AuditRepository append + list (newest first, detail parsed)."""
    db = Database(tmp_path / "audit.db")
    db.setup()
    repo = AuditRepository()
    with db.connect() as conn:
        repo.append(
            conn,
            event="scan_completed",
            actor="system",
            scan_id="s1",
            object_name="Opportunity",
            detail={"finding_count": 10, "coverage_grade": "good", "row_count": 11},
        )
        repo.append(
            conn,
            event="feedback_added",
            actor="tester",
            detail={"finding_id": "f1", "label": "real_issue", "new_status": "acknowledged"},
        )
    with db.connect() as conn:
        entries = repo.list(conn, limit=100)
    assert len(entries) == 2
    assert entries[0]["event"] == "feedback_added"
    assert entries[0]["detail"]["label"] == "real_issue"
    assert entries[1]["event"] == "scan_completed"
    assert entries[1]["detail"]["finding_count"] == 10
    assert entries[1]["detail"]["coverage_grade"] == "good"


def test_audit_via_demo_scan_and_feedback(tmp_path: Path, monkeypatch):
    """End-to-end: demo scan produces scan_completed audit; feedback produces feedback_added."""
    monkeypatch.setenv("SCANNER_DATA_DIR", str(tmp_path))
    import app.main as main
    main = importlib.reload(main)
    with TestClient(main.app) as client:
        # run demo scan
        scan_resp = client.post("/api/scans/demo")
        assert scan_resp.status_code == 200
        scan = scan_resp.json()
        assert scan["status"] == "completed"

        # check audit has scan_completed with exact counts/grade
        audit_resp = client.get("/api/audit")
        assert audit_resp.status_code == 200
        audits = audit_resp.json()
        scan_audits = [a for a in audits if a.get("event") == "scan_completed"]
        assert len(scan_audits) >= 1
        detail = scan_audits[0]["detail"]
        assert detail["finding_count"] == 10
        assert detail["coverage_grade"] == "good"
        assert set(detail.keys()) == {"finding_count", "coverage_grade", "row_count"}
        # sanity: no secrets in values
        assert "token" not in str(detail).lower() and "secret" not in str(detail).lower()

        # pick a finding and add feedback
        findings = client.get(f"/api/findings?scan_id={scan['id']}").json()
        assert len(findings) > 0
        fid = findings[0]["id"]
        fb_resp = client.post(
            f"/api/findings/{fid}/feedback",
            json={"label": "real_issue"},
        )
        assert fb_resp.status_code == 200

        # now audit should include feedback_added
        audit_resp2 = client.get("/api/audit")
        audits2 = audit_resp2.json()
        fb_audits = [a for a in audits2 if a.get("event") == "feedback_added"]
        assert len(fb_audits) >= 1
        fb_detail = fb_audits[0]["detail"]
        assert fb_detail["label"] == "real_issue"
        assert fb_detail["finding_id"] == fid
        assert set(fb_detail.keys()) == {"finding_id", "label", "new_status"}
        assert "token" not in str(fb_detail).lower() and "secret" not in str(fb_detail).lower()
