from __future__ import annotations

import asyncio
import sqlite3
import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.ingestion.sqlite_source import SqliteTableSource

from tests import harness


@pytest.fixture
def sqlite_db(tmp_path: Path) -> Path:
    db_path = tmp_path / "test.db"
    conn = sqlite3.connect(db_path)
    try:
        # customers
        conn.execute("CREATE TABLE customers (id INTEGER PRIMARY KEY, name TEXT NOT NULL)")
        conn.executemany(
            "INSERT INTO customers (id, name) VALUES (?, ?)",
            [(1, "Alice"), (2, "Bob"), (3, "Carol")],
        )
        # orders - note: default foreign_keys=OFF so dangling ok; use ignore for check
        conn.execute(
            "CREATE TABLE orders ("
            "id INTEGER PRIMARY KEY, "
            "customer_id INTEGER NOT NULL REFERENCES customers(id), "
            "status TEXT NOT NULL CHECK (status IN ('new','paid','shipped')), "
            "amount REAL"
            ")"
        )
        conn.execute("PRAGMA ignore_check_constraints = ON")
        # 10 clean
        clean_rows = []
        for i in range(10):
            cust = (i % 3) + 1
            st = ["new", "paid", "shipped"][i % 3]
            amt = 100 + i * 10
            clean_rows.append((cust, st, amt))
        conn.executemany(
            "INSERT INTO orders (customer_id, status, amount) VALUES (?, ?, ?)",
            clean_rows,
        )
        # dangling
        conn.execute(
            "INSERT INTO orders (customer_id, status, amount) VALUES (999, 'new', 123.0)"
        )
        # bogus status (via ignore)
        conn.execute(
            "INSERT INTO orders (customer_id, status, amount) VALUES (1, 'bogus', 456.0)"
        )
        # empty status (passes NOT NULL but required)
        conn.execute(
            "INSERT INTO orders (customer_id, status, amount) VALUES (2, '', 789.0)"
        )
        conn.execute("PRAGMA ignore_check_constraints = OFF")
        conn.commit()
    finally:
        conn.close()
    return db_path


def test_sqlite_schema_translation(sqlite_db: Path):
    src = SqliteTableSource(db_path=sqlite_db, table_name="orders")
    ds = asyncio.run(src.load())
    assert ds.schema is not None
    # Id synthetic
    assert "Id" in ds.schema.fields
    assert ds.schema.fields["Id"].data_type == "id"  # FieldType but str? use value or access
    # customer_id ref required
    assert "customer_id" in ds.schema.fields
    fd = ds.schema.fields["customer_id"]
    assert fd.data_type == "reference"
    assert fd.reference_to == ("customers",)
    assert fd.required is True
    # status picklist required
    fd = ds.schema.fields["status"]
    assert fd.data_type == "picklist"
    assert fd.picklist_values == frozenset({"new", "paid", "shipped"})
    assert fd.required is True
    # amount number
    assert ds.schema.fields["amount"].data_type == "number"
    # fields start with Id
    assert ds.fields[0] == "Id"
    # records are cleaned strings
    assert all(isinstance(r.get("Id"), str) for r in ds.records)
    assert len(ds.records) > 0


def test_sqlite_same_engine_catches_anomalies(sqlite_db: Path):
    src = SqliteTableSource(db_path=sqlite_db, table_name="orders", max_records=100)
    ds = asyncio.run(src.load())
    ctx = harness.context(
        "orders",
        ds.records,
        fields=ds.fields,
        schema=ds.schema,
        related_ids=ds.related_ids,
    )
    findings = harness.detect_all(ctx)
    tcounts = harness.type_counts(findings)
    assert {"picklist_invalid", "orphan_reference", "required_missing"} <= set(tcounts.keys())
    # specific observed
    for f in findings:
        if f.finding_type == "picklist_invalid":
            assert f.evidence.get("observed_value") == "bogus"
        if f.finding_type == "orphan_reference":
            assert f.evidence.get("observed_value") == "999"
    harness.assert_healthy(findings)


def test_sqlite_api_end_to_end(sqlite_db: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    with harness.api_client(tmp_path) as client:
        with open(sqlite_db, "rb") as f:
            files = {"file": ("test.db", f, "application/octet-stream")}
            resp = client.post("/api/scans/database?table_name=orders", files=files)
        assert resp.status_code == 200
        body = resp.json()
        assert body.get("status") == "completed"
        findings = client.get(f"/api/findings?scan_id={body['id']}").json()
        ftypes = {f["finding_type"] for f in findings}
        assert "picklist_invalid" in ftypes
        assert "orphan_reference" in ftypes

        # error: missing table
        with open(sqlite_db, "rb") as f:
            files = {"file": ("test.db", f, "application/octet-stream")}
            resp = client.post("/api/scans/database?table_name=missing_table", files=files)
        assert resp.status_code == 400

        # error: non db
        resp = client.post(
            "/api/scans/database?table_name=orders",
            files={"file": ("bad.txt", b"notdb", "text/plain")},
        )
        assert resp.status_code == 400

        # error: bad table name
        with open(sqlite_db, "rb") as f:
            files = {"file": ("test.db", f, "application/octet-stream")}
            resp = client.post("/api/scans/database?table_name=orders;DROP", files=files)
        assert resp.status_code == 400


def test_sqlite_truncation_honesty(sqlite_db: Path, tmp_path: Path):
    src = SqliteTableSource(db_path=sqlite_db, table_name="orders", max_records=5)
    ds = asyncio.run(src.load())
    assert ds.truncated is True
    scan, _ = harness.run_scan(ds, tmp_path)
    assert scan["coverage"]["grade"] == "partial"
    assert any("record cap" in s for s in scan["coverage"].get("suppressed_checks", []))


def test_sqlite_name_based_format_type_inference(tmp_path: Path):
    """TEXT columns get EMAIL/PHONE/URL from exact name tokens; non-TEXT stays affinity-typed."""
    db_path = tmp_path / "format_types.db"
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            "CREATE TABLE contacts ("
            "email TEXT, "
            "contact_phone TEXT, "
            "website TEXT, "
            "note TEXT, "
            "mail_count INTEGER"
            ")"
        )
        conn.commit()
    finally:
        conn.close()

    src = SqliteTableSource(db_path=db_path, table_name="contacts")
    ds = asyncio.run(src.load())
    assert ds.schema is not None
    assert ds.schema.fields["email"].data_type == "email"
    assert ds.schema.fields["contact_phone"].data_type == "phone"
    assert ds.schema.fields["website"].data_type == "url"
    assert ds.schema.fields["note"].data_type == "string"
    assert ds.schema.fields["mail_count"].data_type == "number"
