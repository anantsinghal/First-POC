from __future__ import annotations

import asyncio
import re
from pathlib import Path

import httpx

from app.api.container import build_container
from app.ingestion.salesforce_source import SalesforceObjectSource
from app.persistence.repositories import AuditRepository, FindingRepository, SyncRepository
from app.salesforce.client import SalesforceClient
from app.salesforce.session import SalesforceSession
from tests import harness


def _describe_payload() -> dict:
    return {
        "name": "Account",
        "fields": [
            {"name": "Id", "type": "id", "queryable": True, "nillable": False},
            {"name": "Name", "type": "string", "queryable": True, "nillable": False},
            {
                "name": "Status",
                "type": "picklist",
                "queryable": True,
                "nillable": True,
                "picklistValues": [
                    {"value": "A", "active": True},
                    {"value": "B", "active": True},
                ],
            },
        ],
    }


def _source(transport: httpx.MockTransport, *, max_records: int = 500):
    session = SalesforceSession("https://sync-test.my.salesforce.com", "FAKE_TOKEN", "v66.0")
    client = SalesforceClient(session, transport=transport)
    return SalesforceObjectSource(
        client=client,
        session=session,
        object_name="Account",
        max_records=max_records,
    )


def test_initial_then_incremental_scan_tracks_ledger_and_watermark(tmp_path: Path):
    queries: list[str] = []

    def initial_handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/sobjects/Account/describe"):
            return httpx.Response(200, json=_describe_payload())
        if request.url.path.endswith("/query"):
            queries.append(request.url.params.get("q", ""))
            return httpx.Response(
                200,
                json={
                    "records": [
                        {"Id": "001", "Name": "Acme", "Status": "A", "attributes": {}},
                        {"Id": "002", "Name": "Beta", "Status": "X", "attributes": {}},
                        {"Id": "003", "Name": "Core", "Status": "B", "attributes": {}},
                    ]
                },
            )
        return httpx.Response(404, json={})

    container = build_container(data_dir=tmp_path, root=harness.ROOT)
    first_scan = asyncio.run(
        container.sync_service.incremental_scan(_source(httpx.MockTransport(initial_handler)))
    )

    assert first_scan["status"] == "completed"
    assert first_scan["sync_mode"] == "initial"
    assert first_scan["changed_records"] == 3
    assert first_scan["row_count"] == 3
    assert queries == ["SELECT Id, Name, Status FROM Account"]

    sync_repo = SyncRepository()
    audit_repo = AuditRepository()
    with container.database.connect() as conn:
        assert sync_repo.ledger_active_ids(conn, "Account") == {"001", "002", "003"}
        watermark_before = sync_repo.get_watermark(conn, "Account")
        assert watermark_before is not None
        audits = [row for row in audit_repo.list(conn, limit=20) if row["event"] == "incremental_scan"]
    assert audits[0]["detail"] == {
        "object_name": "Account",
        "mode": "initial",
        "changed_records": 3,
        "watermark_overlap_minutes": 5,
    }

    incremental_queries: list[str] = []

    def incremental_handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/sobjects/Account/describe"):
            return httpx.Response(200, json=_describe_payload())
        if request.url.path.endswith("/query"):
            q = request.url.params.get("q", "")
            incremental_queries.append(q)
            assert "WHERE SystemModstamp >=" in q
            return httpx.Response(
                200,
                json={"records": [{"Id": "002", "Name": "Beta", "Status": "B", "attributes": {}}]},
            )
        return httpx.Response(404, json={})

    second_scan = asyncio.run(
        container.sync_service.incremental_scan(_source(httpx.MockTransport(incremental_handler)))
    )

    assert second_scan["status"] == "completed"
    assert second_scan["sync_mode"] == "incremental"
    assert second_scan["changed_records"] == 1
    assert second_scan["row_count"] == 1
    assert len(incremental_queries) == 1
    with container.database.connect() as conn:
        assert sync_repo.ledger_active_ids(conn, "Account") == {"001", "002", "003"}
        watermark_after = sync_repo.get_watermark(conn, "Account")
        audits = [row for row in audit_repo.list(conn, limit=20) if row["event"] == "incremental_scan"]
    assert watermark_after is not None
    assert watermark_after >= watermark_before
    assert audits[0]["detail"] == {
        "object_name": "Account",
        "mode": "incremental",
        "changed_records": 1,
        "watermark_overlap_minutes": 5,
    }


def test_reconcile_marks_vanished_records_deleted_and_resolves_findings(tmp_path: Path):
    def initial_handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/sobjects/Account/describe"):
            return httpx.Response(200, json=_describe_payload())
        if request.url.path.endswith("/query"):
            return httpx.Response(
                200,
                json={
                    "records": [
                        {"Id": "001", "Name": "Acme", "Status": "A", "attributes": {}},
                        {"Id": "002", "Name": "Beta", "Status": "X", "attributes": {}},
                        {"Id": "003", "Name": "Core", "Status": "B", "attributes": {}},
                    ]
                },
            )
        return httpx.Response(404, json={})

    container = build_container(data_dir=tmp_path, root=harness.ROOT)
    asyncio.run(container.sync_service.incremental_scan(_source(httpx.MockTransport(initial_handler))))

    finding_repo = FindingRepository()
    sync_repo = SyncRepository()
    audit_repo = AuditRepository()
    with container.database.connect() as conn:
        record_002_findings = [
            row for row in finding_repo.list(conn, status="open") if row["record_id"] == "002"
        ]
    assert len(record_002_findings) >= 1
    assert any(row["finding_type"] == "picklist_invalid" for row in record_002_findings)

    def reconcile_handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/sobjects/Account/describe"):
            return httpx.Response(200, json=_describe_payload())
        if request.url.path.endswith("/query"):
            q = request.url.params.get("q", "")
            assert "WHERE SystemModstamp >=" not in q
            return httpx.Response(
                200,
                json={
                    "records": [
                        {"Id": "001", "Name": "Acme", "Status": "A", "attributes": {}},
                        {"Id": "003", "Name": "Core", "Status": "B", "attributes": {}},
                    ]
                },
            )
        return httpx.Response(404, json={})

    reconciled = asyncio.run(container.sync_service.reconcile(_source(httpx.MockTransport(reconcile_handler))))

    assert reconciled["status"] == "completed"
    assert reconciled["vanished_records"] == 1
    assert reconciled["resolved_findings"] >= 1
    with container.database.connect() as conn:
        row = conn.execute(
            "SELECT is_deleted FROM record_ledger WHERE object_name=? AND record_id=?",
            ("Account", "002"),
        ).fetchone()
        assert row is not None
        assert row["is_deleted"] == 1
        finding_rows = [row for row in finding_repo.list(conn) if row["record_id"] == "002"]
        assert all(row["status"] == "resolved" for row in finding_rows)
        assert sync_repo.ledger_active_ids(conn, "Account") == {"001", "003"}
        audits = [row for row in audit_repo.list(conn, limit=20) if row["event"] == "reconciliation"]
    assert audits[0]["detail"] == {
        "object_name": "Account",
        "records": 2,
        "vanished": 1,
        "resolved_findings": reconciled["resolved_findings"],
    }


def test_incremental_scan_uses_watermark_minus_five_minutes(tmp_path: Path):
    captured_queries: list[str] = []
    container = build_container(data_dir=tmp_path, root=harness.ROOT)
    container.database.setup()
    sync_repo = SyncRepository()
    with container.database.connect() as conn:
        sync_repo.set_watermark(conn, "Account", "2026-07-04T10:00:00+00:00")

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/sobjects/Account/describe"):
            return httpx.Response(200, json=_describe_payload())
        if request.url.path.endswith("/query"):
            captured_queries.append(request.url.params.get("q", ""))
            return httpx.Response(200, json={"records": []})
        return httpx.Response(404, json={})

    scan = asyncio.run(container.sync_service.incremental_scan(_source(httpx.MockTransport(handler))))

    assert scan["sync_mode"] == "incremental"
    assert len(captured_queries) == 1
    match = re.search(r"WHERE SystemModstamp >= (\S+)$", captured_queries[0])
    assert match is not None
    assert match.group(1) == "2026-07-04T09:55:00Z"


def test_sync_repository_upsert_updates_without_duplicates_and_mark_deleted_counts(tmp_path: Path):
    container = build_container(data_dir=tmp_path, root=harness.ROOT)
    container.database.setup()
    sync_repo = SyncRepository()

    with container.database.connect() as conn:
        sync_repo.ledger_upsert_many(
            conn,
            object_name="Account",
            entries=[("001", "hash-a"), ("002", "hash-b")],
            seen_at="2026-07-04T10:00:00+00:00",
        )
        sync_repo.ledger_upsert_many(
            conn,
            object_name="Account",
            entries=[("001", "hash-c")],
            seen_at="2026-07-04T11:00:00+00:00",
        )
        count = sync_repo.ledger_mark_deleted(conn, "Account", ["002", "999"])
        rows = conn.execute(
            """SELECT record_id, first_seen_at, last_seen_at, content_hash, is_deleted
               FROM record_ledger WHERE object_name=? ORDER BY record_id""",
            ("Account",),
        ).fetchall()

    assert count == 1
    assert len(rows) == 2
    assert dict(rows[0]) == {
        "record_id": "001",
        "first_seen_at": "2026-07-04T10:00:00+00:00",
        "last_seen_at": "2026-07-04T11:00:00+00:00",
        "content_hash": "hash-c",
        "is_deleted": 0,
    }
    assert dict(rows[1]) == {
        "record_id": "002",
        "first_seen_at": "2026-07-04T10:00:00+00:00",
        "last_seen_at": "2026-07-04T10:00:00+00:00",
        "content_hash": "hash-b",
        "is_deleted": 1,
    }
