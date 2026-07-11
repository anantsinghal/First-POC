"""Phase 68: ledger pruning on retention purge + optional CORS middleware."""

from __future__ import annotations

import hashlib
import importlib
from pathlib import Path

from fastapi.testclient import TestClient

from app.domain.enums import CoverageGrade
from app.domain.models import Coverage
from app.persistence.database import Database, now
from app.persistence.repositories import (
    AuditRepository,
    CoverageRepository,
    ScanRepository,
    SyncRepository,
)
from app.services.retention_service import RetentionService


def _coverage(object_name: str = "Account") -> Coverage:
    return Coverage(
        object_name=object_name,
        observed_records=1,
        queried_fields=["Id", "Name"],
        missing_fields=[],
        suppressed_checks=[],
        grade=CoverageGrade.GOOD,
        reason="ok",
    )


def test_purge_prunes_orphaned_ledger_and_keeps_active(tmp_path: Path):
    """Purge all scans for an object -> its ledger/sync gone; object with remaining scans keeps them."""
    db = Database(tmp_path / "housekeeping.db")
    db.setup()
    scans = ScanRepository()
    cov = CoverageRepository()
    audit = AuditRepository()
    sync = SyncRepository()
    source_hash = hashlib.sha256(b"hk").hexdigest()
    old_created = "2020-01-01T00:00:00+00:00"
    recent_created = now()

    with db.connect() as conn:
        # Object A: only an old scan (will be purged entirely)
        scans.insert_running(
            conn,
            scan_id="scan-old-a",
            source_name="salesforce:host/Account",
            object_name="Account",
            created_at=old_created,
            row_count=1,
            source_hash=source_hash,
            fields=["Id"],
            source_key="salesforce:host",
        )
        cov.insert(conn, "scan-old-a", _coverage("Account"))
        scans.complete(
            conn, scan_id="scan-old-a", completed_at=old_created, profile={}, notes=""
        )
        sync.set_watermark(conn, "Account", "2020-01-01T00:00:00Z", "salesforce:host")
        sync.ledger_upsert_many(
            conn,
            object_name="Account",
            entries=[("001AAA", "hash-a")],
            seen_at=old_created,
            source_key="salesforce:host",
        )

        # Object B: recent scan remains after purge
        scans.insert_running(
            conn,
            scan_id="scan-recent-b",
            source_name="salesforce:host/Contact",
            object_name="Contact",
            created_at=recent_created,
            row_count=1,
            source_hash=source_hash,
            fields=["Id"],
            source_key="salesforce:host",
        )
        cov.insert(conn, "scan-recent-b", _coverage("Contact"))
        scans.complete(
            conn, scan_id="scan-recent-b", completed_at=recent_created, profile={}, notes=""
        )
        sync.set_watermark(conn, "Contact", "2024-01-01T00:00:00Z", "salesforce:host")
        sync.ledger_upsert_many(
            conn,
            object_name="Contact",
            entries=[("003BBB", "hash-b")],
            seen_at=recent_created,
            source_key="salesforce:host",
        )

    svc = RetentionService(database=db, scans=scans, audit=audit)
    result = svc.purge(older_than_days=30)

    assert result["purged_scans"] == 1
    assert "ledger_pruned" in result
    assert result["ledger_pruned"] >= 1  # Account ledger row + Account sync_state

    with db.connect() as conn:
        a_ids = sync.ledger_active_ids(conn, "Account", "salesforce:host")
        b_ids = sync.ledger_active_ids(conn, "Contact", "salesforce:host")
        assert a_ids == set()
        assert b_ids == {"003BBB"}
        assert sync.get_watermark(conn, "Account", "salesforce:host") is None
        assert sync.get_watermark(conn, "Contact", "salesforce:host") == "2024-01-01T00:00:00Z"
        assert scans.get(conn, "scan-old-a") is None
        assert scans.get(conn, "scan-recent-b") is not None


def test_cors_on_with_env(tmp_path: Path, monkeypatch):
    """SCANNER_CORS_ORIGINS set -> OPTIONS preflight returns Access-Control-Allow-Origin."""
    monkeypatch.setenv("SCANNER_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("SCANNER_CORS_ORIGINS", "http://localhost:3000")
    monkeypatch.delenv("SCANNER_API_KEY", raising=False)
    import app.main as main

    main = importlib.reload(main)
    with TestClient(main.app) as client:
        r = client.options(
            "/api/findings",
            headers={
                "Origin": "http://localhost:3000",
                "Access-Control-Request-Method": "GET",
            },
        )
        assert r.headers.get("access-control-allow-origin") == "http://localhost:3000"


def test_cors_off_without_env(tmp_path: Path, monkeypatch):
    """Without SCANNER_CORS_ORIGINS, no CORS allow-origin header (today's behavior)."""
    monkeypatch.setenv("SCANNER_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("SCANNER_CORS_ORIGINS", raising=False)
    monkeypatch.delenv("SCANNER_API_KEY", raising=False)
    import app.main as main

    main = importlib.reload(main)
    with TestClient(main.app) as client:
        r = client.options(
            "/api/findings",
            headers={
                "Origin": "http://localhost:3000",
                "Access-Control-Request-Method": "GET",
            },
        )
        assert "access-control-allow-origin" not in {
            k.lower(): v for k, v in r.headers.items()
        }
        # header lookup is case-insensitive on Starlette; also check direct get
        assert r.headers.get("access-control-allow-origin") is None
