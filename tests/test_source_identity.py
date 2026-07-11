from __future__ import annotations

import asyncio
import sqlite3
from pathlib import Path

from app.api.container import build_container
from app.ingestion.sqlite_source import SqliteTableSource
from app.persistence.database import Database
from app.persistence.source_key import source_key_from_name
from app.domain.models import stable_hash
from tests import harness


# --- (a) Unit: source_key_from_name for the 5 documented shapes ---

def test_source_key_from_name_shapes():
    assert source_key_from_name("salesforce:host/Object") == "salesforce:host"
    assert source_key_from_name("salesforce-bulk:host/Object") == "salesforce-bulk:host"
    assert source_key_from_name("sqlite:file.db/table") == "sqlite:file.db"
    assert source_key_from_name("demo_opportunities.csv") == "src:demo_opportunities.csv"
    assert source_key_from_name("multi:Account+2 related") == "src:multi:Account+2 related"
    # bonus: stream:<name> (also documented, no '/' so no shape match)
    assert source_key_from_name("stream:Account") == "src:stream:Account"


# --- Shared fixture builder for (b)/(c)/(e) ---

def _build_orders_db(db_path: Path, status_value: str = "bogus") -> None:
    """A single-row 'orders' table whose CHECK constraint makes 'status' a picklist field,
    with one row (id=1) violating it -> a deterministic picklist_invalid finding on record Id='1'.
    """
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            "CREATE TABLE orders ("
            "id INTEGER PRIMARY KEY, "
            "status TEXT NOT NULL CHECK (status IN ('open','closed')), "
            "name TEXT"
            ")"
        )
        conn.execute("PRAGMA ignore_check_constraints = ON")
        conn.execute(
            "INSERT INTO orders (id, status, name) VALUES (1, ?, 'Row One')", (status_value,)
        )
        conn.execute("PRAGMA ignore_check_constraints = OFF")
        conn.commit()
    finally:
        conn.close()


def _picklist_findings(findings: list[dict]) -> list[dict]:
    return [
        f for f in findings
        if f["finding_type"] == "picklist_invalid" and f["record_id"] == "1" and f["object_name"] == "orders"
    ]


# --- (b) THE collision test ---

def test_collision_across_two_sources_coexist(tmp_path: Path):
    """Two different SQLite dbs both have table 'orders' with a bad row id=1. Pre-phase-63, the
    fingerprint hash(object,record,rule,field) collided across sources and the second scan's
    upsert would silently overwrite the first source's finding. Scoping by source_key must keep
    both as distinct rows.
    """
    data_dir = tmp_path / "data"
    db_a = tmp_path / "db_a.sqlite"
    db_b = tmp_path / "db_b.sqlite"
    _build_orders_db(db_a)
    _build_orders_db(db_b)

    container = build_container(data_dir=data_dir, root=harness.ROOT)
    container.database.setup()

    scan_a = asyncio.run(
        container.scan_service.run_scan(SqliteTableSource(db_path=db_a, table_name="orders", max_records=100))
    )
    scan_b = asyncio.run(
        container.scan_service.run_scan(SqliteTableSource(db_path=db_b, table_name="orders", max_records=100))
    )
    assert scan_a["status"] == "completed"
    assert scan_b["status"] == "completed"

    findings = container.finding_service.list_findings()
    picklist_findings = _picklist_findings(findings)

    assert len(picklist_findings) == 2, f"expected 2 distinct source-scoped findings, got: {picklist_findings}"
    fingerprints = {f["fingerprint"] for f in picklist_findings}
    assert len(fingerprints) == 2, "the two findings must NOT share a fingerprint"
    scan_ids = {f["scan_id"] for f in picklist_findings}
    assert scan_ids == {scan_a["id"], scan_b["id"]}


# --- (c) Drift isolation ---

def test_drift_baseline_never_crosses_sources(tmp_path: Path):
    """db-A scanned twice establishes a drift baseline for db-A; db-B's (first) scan must never
    pick up db-A's scan as its baseline, even though both objects are named 'orders'.
    """
    data_dir = tmp_path / "data"
    db_a = tmp_path / "db_a.sqlite"
    db_b = tmp_path / "db_b.sqlite"
    _build_orders_db(db_a)
    _build_orders_db(db_b)

    container = build_container(data_dir=data_dir, root=harness.ROOT)
    container.database.setup()

    def src_a() -> SqliteTableSource:
        return SqliteTableSource(db_path=db_a, table_name="orders", max_records=100)

    def src_b() -> SqliteTableSource:
        return SqliteTableSource(db_path=db_b, table_name="orders", max_records=100)

    scan_a1 = asyncio.run(container.scan_service.run_scan(src_a()))
    scan_a2 = asyncio.run(container.scan_service.run_scan(src_a()))
    scan_b1 = asyncio.run(container.scan_service.run_scan(src_b()))

    # Sanity: db-A's second scan DOES pick up db-A's first scan as baseline (same-source drift works).
    drift_a2 = container.drift_service.compare(scan_a2["id"])
    assert drift_a2["baseline_scan_id"] == scan_a1["id"]

    # db-B's first scan must never cross-pick db-A's scans as its baseline: it's db-B's first
    # scan ever, so the honest answer is "no baseline" (None) — and in particular never db-A's.
    drift_b1 = container.drift_service.compare(scan_b1["id"])
    assert drift_b1["baseline_scan_id"] not in (scan_a1["id"], scan_a2["id"])
    assert drift_b1["baseline_scan_id"] is None


# --- (d) Migration ---

def test_migration_recomputes_legacy_fingerprint_and_bumps_version(tmp_path: Path):
    """A v1-shaped db (pre-source-scoping finding row, source_key='', old-format fingerprint,
    user_version=1) must, on the next setup(), get its source_key backfilled ('src:legacy' since
    the finding's scan_id has no matching manifest here) and its fingerprint recomputed to the
    new source-scoped format, then land on user_version=2.
    """
    db = Database(tmp_path / "legacy.db")
    db.setup()  # a fresh DB is already v2-shaped; we simulate a v1 DB by rolling user_version back

    old_fingerprint = stable_hash({
        "object": "Account", "record": "001XXX", "rule": "picklist_invalid", "field": "Status",
    })
    with db.connect() as conn:
        conn.execute(
            """INSERT INTO findings (
                   id, fingerprint, scan_id, object_name, record_id, record_name, finding_type,
                   field_name, severity, confidence, priority, status, title, evidence_json,
                   source_hash, created_at, last_seen_at, source_key
               ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                "f-legacy", old_fingerprint, "scan-legacy-orphan", "Account", "001XXX", "Acme",
                "picklist_invalid", "Status", "medium", 0.8, 0.5, "open", "Bad picklist", "{}",
                "sourcehash", "2026-01-01T00:00:00+00:00", "2026-01-01T00:00:00+00:00", "",
            ),
        )
        conn.execute("PRAGMA user_version=1")

    db.setup()

    with db.connect() as conn:
        row = conn.execute(
            "SELECT source_key, fingerprint FROM findings WHERE id='f-legacy'"
        ).fetchone()
        version = conn.execute("PRAGMA user_version").fetchone()[0]

    assert row["source_key"] == "src:legacy"
    assert row["fingerprint"] != old_fingerprint
    expected_new_fp = stable_hash({
        "source": "src:legacy", "object": "Account", "record": "001XXX",
        "rule": "picklist_invalid", "field": "Status",
    })
    assert row["fingerprint"] == expected_new_fp
    assert version == 2


# --- (e) Feedback survival ---

def test_feedback_survives_rescan_of_same_source(tmp_path: Path):
    """Labeling a finding, then re-running the exact same source, must keep the label's
    suppression in effect (the fingerprint must be stable across re-scans of the same source).
    """
    data_dir = tmp_path / "data"
    db_a = tmp_path / "db_a.sqlite"
    _build_orders_db(db_a)

    container = build_container(data_dir=data_dir, root=harness.ROOT)
    container.database.setup()

    def src() -> SqliteTableSource:
        return SqliteTableSource(db_path=db_a, table_name="orders", max_records=100)

    scan1 = asyncio.run(container.scan_service.run_scan(src()))
    findings1 = _picklist_findings(container.finding_service.list_findings(scan_id=scan1["id"]))
    assert len(findings1) == 1
    finding_id = findings1[0]["id"]
    fingerprint = findings1[0]["fingerprint"]

    container.finding_service.add_feedback(
        finding_id, label="false_positive", note="not a real issue", reviewer="tester"
    )

    scan2 = asyncio.run(container.scan_service.run_scan(src()))
    findings2 = _picklist_findings(container.finding_service.list_findings(scan_id=scan2["id"]))
    assert len(findings2) == 1
    assert findings2[0]["id"] == finding_id
    assert findings2[0]["fingerprint"] == fingerprint
    assert findings2[0]["status"] == "suppressed_until"
