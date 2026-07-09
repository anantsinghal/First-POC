"""API security: optional SCANNER_API_KEY guard + open health/static shell."""

from __future__ import annotations

import inspect
from pathlib import Path

from tests import harness


def test_auth_off_by_default(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("SCANNER_API_KEY", raising=False)
    with harness.api_client(tmp_path) as client:
        assert client.post("/api/scans/demo").status_code == 200
        assert client.get("/api/dashboard").status_code == 200


def test_auth_on_requires_key(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("SCANNER_API_KEY", "test-secret-123")
    with harness.api_client(tmp_path) as client:
        assert client.post("/api/scans/demo").status_code == 401
        assert client.post(
            "/api/scans/demo", headers={"X-API-Key": "wrong-key"}
        ).status_code == 401
        assert client.post(
            "/api/scans/demo", headers={"X-API-Key": "test-secret-123"}
        ).status_code == 200
        assert client.post(
            "/api/admin/purge", json={"older_than_days": 1}
        ).status_code == 401
        assert client.get("/api/findings").status_code == 401


def test_health_stays_open_when_auth_on(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("SCANNER_API_KEY", "test-secret-123")
    with harness.api_client(tmp_path) as client:
        r = client.get("/api/health")
        assert r.status_code == 200
        assert r.json()["status"] == "ok"


def test_static_ui_shell_open_when_auth_on(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("SCANNER_API_KEY", "test-secret-123")
    with harness.api_client(tmp_path) as client:
        assert client.get("/").status_code == 200


def test_guard_uses_hmac_compare_digest():
    import app.api.security as security

    src = inspect.getsource(security)
    assert "hmac.compare_digest" in src
    assert "api_key_guard" in src
