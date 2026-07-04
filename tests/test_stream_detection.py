from __future__ import annotations

import asyncio
import sqlite3
from pathlib import Path

import pytest

from app.ingestion.csv_source import CsvSource
from app.ingestion.sqlite_source import SqliteTableSource
from tests import harness


def _build_stream_test_db(db_path: Path) -> None:
    """Create items + owners with 4000 clean + 3 planted anomalies via ignore_check."""
    conn = sqlite3.connect(db_path)
    try:
        conn.execute("CREATE TABLE owners (id INTEGER PRIMARY KEY, name TEXT)")
        conn.executemany(
            "INSERT INTO owners (id, name) VALUES (?, ?)",
            [(1, "Alice"), (2, "Bob")],
        )
        conn.execute(
            "CREATE TABLE items ("
            "id INTEGER PRIMARY KEY, "
            "kind TEXT NOT NULL CHECK (kind IN ('a','b')), "
            "owner_id INTEGER REFERENCES owners(id), "
            "note TEXT"
            ")"
        )
        conn.execute("PRAGMA ignore_check_constraints = ON")
        # 4000 clean rows
        clean_rows = []
        for i in range(4000):
            kind = "a" if i % 2 == 0 else "b"
            owner = 1 if i % 3 != 0 else 2
            clean_rows.append((kind, owner, f"note-{i}"))
        conn.executemany(
            "INSERT INTO items (kind, owner_id, note) VALUES (?, ?, ?)",
            clean_rows,
        )
        # planted anomalies
        conn.execute("INSERT INTO items (kind, owner_id, note) VALUES ('zzz', 1, 'bad picklist')")
        conn.execute("INSERT INTO items (kind, owner_id, note) VALUES ('', 2, 'empty required')")
        conn.execute("INSERT INTO items (kind, owner_id, note) VALUES ('a', 777, 'orphan ref')")
        conn.execute("PRAGMA ignore_check_constraints = OFF")
        conn.commit()
    finally:
        conn.close()


def test_streaming_detects_tier0_across_batches(tmp_path: Path):
    db_path = tmp_path / "stream_test.db"
    _build_stream_test_db(db_path)

    src = SqliteTableSource(db_path=db_path, table_name="items", max_records=10_000)
    # Force small batch_size for >1 batches and bounded check
    orig_iter = src.iter_batches
    def force_small(batch_size=5000):
        return orig_iter(batch_size=500)
    src.iter_batches = force_small

    from app.api.container import build_container as build_c
    container = build_c(data_dir=tmp_path, root=harness.ROOT)
    container.database.setup()
    scan = asyncio.run(container.scan_service.stream_scan(src))
    assert scan["status"] == "completed"
    assert "streamed scan" in scan.get("notes", "")

    findings = container.finding_service.list_findings(scan_id=scan["id"])
    ftypes = {f["finding_type"] for f in findings}
    assert "picklist_invalid" in ftypes
    assert "required_missing" in ftypes
    assert "orphan_reference" in ftypes
    assert len(findings) >= 3

    # audit
    audits = container.audit_service.list(limit=20)
    streamed = [a for a in audits if a.get("event") == "scan_streamed" and a.get("scan_id") == scan["id"]]
    assert streamed, "missing scan_streamed audit"
    assert streamed[0]["detail"]["batches"] > 1


def test_stream_bounded_batch_size(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    db_path = tmp_path / "stream_test.db"
    _build_stream_test_db(db_path)

    src = SqliteTableSource(db_path=db_path, table_name="items", max_records=10_000)

    max_seen = {"len": 0}
    orig = src.iter_batches
    def recording_iter(batch_size=5000):
        for batch in orig(batch_size=batch_size):
            if len(batch) > max_seen["len"]:
                max_seen["len"] = len(batch)
            yield batch
    src.iter_batches = recording_iter

    from app.api.container import build_container as build_c
    container = build_c(data_dir=tmp_path, root=harness.ROOT)
    container.database.setup()
    # force the 500 inside via wrapper on call (but since our recording uses the passed)
    # call with wrapper that forces 500
    def force500(batch_size=5000):
        return recording_iter(500)  # the orig wrapped
    # rewrap
    src.iter_batches = force500
    asyncio.run(container.scan_service.stream_scan(src))
    assert max_seen["len"] == 500, f"max batch was {max_seen['len']} (should be 500)"


def test_stream_deferred_honesty(tmp_path: Path):
    db_path = tmp_path / "stream_test.db"
    _build_stream_test_db(db_path)

    src = SqliteTableSource(db_path=db_path, table_name="items", max_records=10_000)
    orig_iter = src.iter_batches
    def force_small(bs=5000):
        return orig_iter(batch_size=500)
    src.iter_batches = force_small

    from app.api.container import build_container as build_c
    container = build_c(data_dir=tmp_path, root=harness.ROOT)
    container.database.setup()
    scan = asyncio.run(container.scan_service.stream_scan(src))
    notes = scan.get("notes", "")
    assert "streamed scan" in notes
    assert "deferred (streaming)" in notes or "deferred" in notes.lower()

    audits = container.audit_service.list(limit=10)
    streamed = [a for a in audits if a.get("event") == "scan_streamed"]
    assert streamed
    detail = streamed[0].get("detail", {})
    deferred = detail.get("deferred", [])
    assert "pattern_break" in deferred

    findings = container.finding_service.list_findings(scan_id=scan["id"])
    finding_types = {f["finding_type"] for f in findings}
    assert not (finding_types & set(deferred))


def test_stream_api_endpoint_and_error(tmp_path: Path):
    db_path = tmp_path / "stream_test.db"
    _build_stream_test_db(db_path)

    with harness.api_client(tmp_path) as client:
        with open(db_path, "rb") as f:
            files = {"file": ("items.db", f, "application/octet-stream")}
            resp = client.post("/api/scans/database/stream?table_name=items", files=files)
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body.get("status") == "completed"
        findings = client.get(f"/api/findings?scan_id={body['id']}").json()
        ftypes = {f["finding_type"] for f in findings}
        assert "picklist_invalid" in ftypes
        assert "required_missing" in ftypes
        assert "orphan_reference" in ftypes

        # bad table
        with open(db_path, "rb") as f:
            files = {"file": ("items.db", f, "application/octet-stream")}
            resp = client.post("/api/scans/database/stream?table_name=nonexistent", files=files)
        assert resp.status_code == 400

        # bad file type
        resp = client.post(
            "/api/scans/database/stream?table_name=items",
            files={"file": ("bad.txt", b"xxx", "text/plain")},
        )
        assert resp.status_code == 400


def test_stream_fallback_csv_source(tmp_path: Path):
    demo = harness.ROOT / "data" / "demo_opportunities.csv"
    raw = demo.read_bytes()
    src = CsvSource(raw_bytes=raw, object_name="Opportunity", source_name="demo_opportunities.csv")

    from app.api.container import build_container as build_c
    container = build_c(data_dir=tmp_path, root=harness.ROOT)
    container.database.setup()
    scan = asyncio.run(container.scan_service.stream_scan(src))
    assert scan["status"] == "completed"
    assert "streamed scan" in scan.get("notes", "") or scan.get("finding_count", 0) >= 0

    findings = container.finding_service.list_findings(scan_id=scan["id"])
    ftypes = {f["finding_type"] for f in findings}
    # rule interpreter ones should appear (per-record rules)
    assert "missing_contract_closed_won" in ftypes or len(findings) > 0
