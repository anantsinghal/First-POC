"""Phase 67: reviewer identity on feedback + throttled auth_failed audit."""

from __future__ import annotations

import json
from pathlib import Path

from tests import harness


def test_feedback_reviewer_flows_into_metrics(tmp_path: Path, monkeypatch):
    """POST feedback with reviewer 'alice' -> feedback row + metrics labels_by_reviewer alice=1."""
    monkeypatch.delenv("SCANNER_API_KEY", raising=False)
    with harness.api_client(tmp_path) as client:
        scan = client.post("/api/scans/demo").json()
        findings = client.get(f"/api/findings?scan_id={scan['id']}").json()
        assert findings, "demo scan should produce findings"
        fid = findings[0]["id"]

        r = client.post(
            f"/api/findings/{fid}/feedback",
            json={"label": "real_issue", "reviewer": "alice"},
        )
        assert r.status_code == 200

        detail = client.get(f"/api/findings?scan_id={scan['id']}").json()
        labeled = next(f for f in detail if f["id"] == fid)
        assert any(fb.get("reviewer") == "alice" for fb in labeled.get("feedback") or [])

        metrics = client.get("/api/metrics").json()
        assert "labels_by_reviewer" in metrics
        assert metrics["labels_by_reviewer"].get("alice") == 1


def test_auth_failed_audited_without_secrets(tmp_path: Path, monkeypatch):
    """With SCANNER_API_KEY set, one bad-key request -> auth_failed audit with path, no key text."""
    secret = "super-secret-key-xyz-phase67"
    monkeypatch.setenv("SCANNER_API_KEY", secret)
    with harness.api_client(tmp_path) as client:
        r = client.get("/api/findings", headers={"X-API-Key": "wrong-key-value"})
        assert r.status_code == 401

        audits = client.get(
            "/api/audit?limit=50", headers={"X-API-Key": secret}
        ).json()
        failed = [a for a in audits if a.get("event") == "auth_failed"]
        assert len(failed) >= 1
        entry = failed[0]
        assert entry.get("actor") == "unknown"
        detail = entry.get("detail") or {}
        assert detail.get("path") == "/api/findings"

        detail_blob = json.dumps(detail)
        assert secret not in detail_blob
        assert "wrong-key-value" not in detail_blob
        assert "X-API-Key" not in detail_blob
        assert "api_key" not in detail_blob.lower() or "api_key" not in json.dumps(entry).lower()


def test_auth_failed_throttle(tmp_path: Path, monkeypatch):
    """Burst of 5 bad requests within the 10s window -> at most 2 auth_failed audit rows."""
    secret = "throttle-secret-phase67"
    monkeypatch.setenv("SCANNER_API_KEY", secret)
    with harness.api_client(tmp_path) as client:
        for _ in range(5):
            r = client.get("/api/findings", headers={"X-API-Key": "bad"})
            assert r.status_code == 401

        audits = client.get(
            "/api/audit?limit=100", headers={"X-API-Key": secret}
        ).json()
        failed = [a for a in audits if a.get("event") == "auth_failed"]
        assert len(failed) <= 2
        assert len(failed) >= 1
        for entry in failed:
            blob = json.dumps(entry)
            assert secret not in blob
            assert "bad" not in (entry.get("detail") or {})
