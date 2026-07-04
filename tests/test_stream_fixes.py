from __future__ import annotations

import asyncio
import sqlite3
from pathlib import Path

import httpx
import pytest

from app.api.container import build_container as build_c
from app.detection.base import DetectionContext
from app.detection.schema_driven.format_validity import FormatValidityDetector
from app.domain.schema import FieldDescriptor, FieldType, ObjectSchema
from app.ingestion.salesforce_bulk_source import SalesforceBulkSource
from app.ingestion.sqlite_source import SqliteTableSource
from app.persistence.repositories import FindingRepository, ScanRepository
from app.profiling.columnar_profiler import ColumnarProfiler
from app.policy.field_policy import DEFAULT_POLICY
from app.salesforce import SalesforceClient, SalesforceSession
from app.staging.parquet_store import BatchParquetWriter
from tests import harness


def _make_account_describe():
    return {
        "name": "Account",
        "fields": [
            {"name": "Id", "type": "id", "queryable": True, "nillable": False},
            {"name": "Name", "type": "string", "queryable": True, "nillable": False},
            {
                "name": "Type",
                "type": "picklist",
                "queryable": True,
                "nillable": True,
                "picklistValues": [
                    {"value": "Customer", "active": True},
                    {"value": "Partner", "active": True},
                ],
            },
            {"name": "Website", "type": "url", "queryable": True, "nillable": True},
        ],
    }


PAGE1 = (
    "Id,Name,Type,Website\r\n"
    "001001,Acme,Customer,https://acme.example\r\n"
    "001002,Globex,Frozen,https://g.example\r\n"
    "001003,,Partner,https://p.example\r\n"
)
PAGE2 = (
    "Id,Name,Type,Website\r\n"
    "001004,Delta,Customer,https://d.example\r\n"
    "001005,Echo,Partner,https://e.example\r\n"
)


def _make_stateful_handler():
    status_count = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        query = str(request.url.query or "")
        method = request.method.upper()
        if path.endswith("/sobjects/Account/describe"):
            return httpx.Response(200, json=_make_account_describe())
        if path.endswith("/jobs/query") and method == "POST":
            return httpx.Response(200, json={"id": "750XX", "state": "UploadComplete"})
        if path.endswith("/jobs/query/750XX") and "/results" not in path:
            status_count["n"] += 1
            if status_count["n"] <= 1:
                return httpx.Response(200, json={"state": "InProgress"})
            return httpx.Response(200, json={"state": "JobComplete"})
        if path.endswith("/jobs/query/750XX/results"):
            if "locator=LOC2" in query:
                return httpx.Response(200, text=PAGE2, headers={"Sforce-Locator": "null"})
            return httpx.Response(200, text=PAGE1, headers={"Sforce-Locator": "LOC2"})
        return httpx.Response(200, json={})

    return handler


def _build_checkpoint_db(db_path: Path) -> None:
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
        rows = []
        for i in range(2000):
            kind = "zzz" if i in {100, 600, 900, 1400, 1700} else ("a" if i % 2 == 0 else "b")
            owner_id = 777 if i in {250, 750, 1250, 1750} else (1 if i % 3 else 2)
            rows.append((kind, owner_id, f"note-{i}"))
        conn.executemany(
            "INSERT INTO items (kind, owner_id, note) VALUES (?, ?, ?)",
            rows,
        )
        conn.execute("PRAGMA ignore_check_constraints = OFF")
        conn.commit()
    finally:
        conn.close()


def test_batch_parquet_writer_roundtrip(tmp_path: Path) -> None:
    pytest.importorskip("pyarrow")
    pytest.importorskip("duckdb")

    path = tmp_path / "batches.parquet"
    columns = ["Id", "Name", "Website"]

    with BatchParquetWriter(path, columns) as writer:
        for batch_index in range(3):
            start = batch_index * 100
            writer.write(
                [
                    {
                        "Id": f"001{row_index:03d}",
                        "Name": f"Account {row_index}",
                        "Website": f"www{row_index}.example.com",
                    }
                    for row_index in range(start, start + 100)
                ]
            )

    profile = ColumnarProfiler().profile(path, columns)
    assert profile.record_count == 300


def test_async_bulk_stream_writes_one_parquet_batch_per_stream_batch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pytest.importorskip("pyarrow")
    pytest.importorskip("duckdb")

    handler = _make_stateful_handler()
    session = SalesforceSession("https://example.my.salesforce.com", "FAKE")
    client = SalesforceClient(session, transport=httpx.MockTransport(handler))
    src = SalesforceBulkSource(
        client=client,
        session=session,
        object_name="Account",
        max_records=100000,
        poll_interval=0,
        max_polls=10,
        batch_size=2,
    )
    container = build_c(data_dir=tmp_path, root=harness.ROOT)
    container.database.setup()

    calls = {"count": 0}
    original_write = BatchParquetWriter.write

    def counting_write(self, records):
        calls["count"] += 1
        return original_write(self, records)

    monkeypatch.setattr(BatchParquetWriter, "write", counting_write)

    scan = asyncio.run(container.scan_service.stream_scan(src))
    audits = container.audit_service.list(limit=20)
    streamed = [a for a in audits if a.get("event") == "scan_streamed" and a.get("scan_id") == scan["id"]]
    assert streamed
    assert streamed[0]["detail"]["batches"] >= 2
    assert calls["count"] == streamed[0]["detail"]["batches"]


def test_stream_scan_checkpoints_findings_before_crash(tmp_path: Path) -> None:
    db_path = tmp_path / "checkpoint.db"
    _build_checkpoint_db(db_path)

    src = SqliteTableSource(db_path=db_path, table_name="items", max_records=10_000)
    original_iter = src.iter_batches

    def crash_after_two_batches(batch_size=5000):
        yielded = 0
        for batch in original_iter(batch_size=500):
            if yielded >= 2:
                raise RuntimeError("boom after two batches")
            yielded += 1
            yield batch

    src.iter_batches = crash_after_two_batches

    container = build_c(data_dir=tmp_path, root=harness.ROOT)
    container.database.setup()

    with pytest.raises(RuntimeError, match="boom after two batches"):
        asyncio.run(container.scan_service.stream_scan(src))

    scan_repo = ScanRepository()
    findings = FindingRepository()
    with container.database.connect() as conn:
        crashed_scan_id = scan_repo.latest_id(conn)
        assert crashed_scan_id is not None
        manifest = scan_repo.get(conn, crashed_scan_id)
        assert manifest is not None
        assert manifest["status"] == "running"
        assert findings.count_for_scan(conn, crashed_scan_id) > 0

    clean_src = SqliteTableSource(db_path=db_path, table_name="items", max_records=10_000)
    clean_scan = asyncio.run(container.scan_service.stream_scan(clean_src))
    assert clean_scan["status"] == "completed"
    assert clean_scan["id"] != crashed_scan_id


def test_schemeless_urls_are_valid_and_invalid_strings_still_flag() -> None:
    schema = ObjectSchema(
        object_name="Account",
        fields={"Website": FieldDescriptor(name="Website", data_type=FieldType.URL)},
    )
    records = [
        {"Id": "1", "Website": "www.pyramid.com"},
        {"Id": "2", "Website": "dickenson-consulting.com"},
        {"Id": "3", "Website": "sub.example.co/path"},
        {"Id": "4", "Website": "/services/images/photo/001J200000K1PXYIA3"},
        {"Id": "5", "Website": "https://example.com/path"},
        {"Id": "6", "Website": "nope"},
        {"Id": "7", "Website": "not a url"},
        {"Id": "8", "Website": "http://"},
    ]
    ctx = DetectionContext(
        object_name="Account",
        records=records,
        fields=["Id", "Website"],
        policy=DEFAULT_POLICY,
        schema=schema,
    )

    findings = FormatValidityDetector().detect(ctx)
    flagged_ids = {finding.record_id for finding in findings}
    assert flagged_ids == {"6", "7", "8"}

