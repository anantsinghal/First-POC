from __future__ import annotations

import asyncio
import json
from pathlib import Path

import httpx

from app.api.container import build_container
from app.ingestion.salesforce_bulk_source import SalesforceBulkSource
from app.persistence.repositories import AuditRepository, SyncRepository
from app.persistence.source_key import source_key_from_name
from app.salesforce import SalesforceClient, SalesforceSession
from tests import harness

# Matches SalesforceBulkSource.source_name (set at construction) for this test's fixed session host.
BULK_SOURCE_KEY = source_key_from_name("salesforce-bulk:bulk-inc-test.my.salesforce.com/Account")

_SOBJECTS = [
    {"name": "Small__c", "custom": True, "queryable": True, "deprecatedAndHidden": False},
    {"name": "Big__c", "custom": True, "queryable": True, "deprecatedAndHidden": False},
    {"name": "Empty__c", "custom": True, "queryable": True, "deprecatedAndHidden": False},
    {"name": "Broken__c", "custom": True, "queryable": True, "deprecatedAndHidden": False},
    {"name": "AccountChangeEvent", "custom": False, "queryable": True, "deprecatedAndHidden": False},
    {"name": "Hidden__c", "custom": True, "queryable": False, "deprecatedAndHidden": False},
]

BIG_PAGE1 = "Id,Name,Status__c\r\n001BIG001,Big One,A\r\n001BIG002,Big Two,ZZZ\r\n"
BIG_PAGE2 = "Id,Name,Status__c\r\n001BIG003,Big Three,B\r\n"


def _describe(name: str) -> dict:
    return {
        "name": name,
        "queryable": True,
        "fields": [
            {"name": "Id", "type": "id", "queryable": True, "nillable": False},
            {"name": "Name", "type": "string", "queryable": True, "nillable": False},
            {
                "name": "Status__c",
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


def _make_org_handler():
    """Stateful handler simulating describe_global, per-object describes, COUNT queries,
    a plain SOQL query for Small__c, and a full Bulk API 2.0 job lifecycle for Big__c.
    """

    def handler(request: httpx.Request) -> httpx.Response:
        p = request.url.path
        method = request.method.upper()
        qs = str(request.url.query or "")
        q = request.url.params.get("q", "") or ""

        if p.endswith("/sobjects"):
            return httpx.Response(200, json={"sobjects": _SOBJECTS})
        if p.endswith("/sobjects/Small__c/describe"):
            return httpx.Response(200, json=_describe("Small__c"))
        if p.endswith("/sobjects/Big__c/describe"):
            return httpx.Response(200, json=_describe("Big__c"))
        if p.endswith("/query") and q.startswith("SELECT COUNT()"):
            if "FROM Small__c" in q:
                return httpx.Response(200, json={"totalSize": 40})
            if "FROM Big__c" in q:
                return httpx.Response(200, json={"totalSize": 25_000})
            if "FROM Empty__c" in q:
                return httpx.Response(200, json={"totalSize": 0})
            if "FROM Broken__c" in q:
                return httpx.Response(400, json={"message": "object unavailable"})
            return httpx.Response(200, json={"totalSize": 0})
        if p.endswith("/query") and "FROM Small__c" in q:
            rows = [
                {
                    "Id": f"00S{i:03d}",
                    "Name": f"Item {i}",
                    "Status__c": "ZZZ" if i == 0 else "A",
                    "attributes": {},
                }
                for i in range(40)
            ]
            return httpx.Response(200, json={"records": rows})
        if p.endswith("/jobs/query") and method == "POST":
            return httpx.Response(200, json={"id": "750BIG", "state": "UploadComplete"})
        if p.endswith("/jobs/query/750BIG") and "/results" not in p:
            return httpx.Response(200, json={"state": "JobComplete"})
        if p.endswith("/jobs/query/750BIG/results"):
            if "locator=LOC2" in qs:
                return httpx.Response(200, text=BIG_PAGE2, headers={"Sforce-Locator": "null"})
            return httpx.Response(200, text=BIG_PAGE1, headers={"Sforce-Locator": "LOC2"})
        return httpx.Response(404, json={})

    return handler


def _client_and_session(handler) -> tuple[SalesforceClient, SalesforceSession]:
    session = SalesforceSession("https://org-test.my.salesforce.com", "FAKE_TOKEN", "v66.0")
    client = SalesforceClient(session, transport=httpx.MockTransport(handler))
    return client, session


def test_scan_org_routes_and_is_resilient(tmp_path: Path):
    client, session = _client_and_session(_make_org_handler())
    container = build_container(data_dir=tmp_path, root=harness.ROOT)

    summary = asyncio.run(
        container.orchestrator_service.scan_org(client=client, session=session, stream_threshold=10_000)
    )

    scanned_by_object = {s["object"]: s for s in summary["scanned"]}
    assert scanned_by_object["Small__c"]["route"] == "memory"
    assert scanned_by_object["Big__c"]["route"] == "bulk-stream"

    skipped_reasons = {s["object"]: s["reason"] for s in summary["skipped"]}
    assert skipped_reasons["Empty__c"] == "no records"
    assert skipped_reasons["AccountChangeEvent"] == "system child object"
    assert skipped_reasons["Hidden__c"] == "not queryable"

    failed_objects = {f["object"] for f in summary["failed"]}
    assert "Broken__c" in failed_objects

    assert summary["total_findings"] >= 2
    assert summary["objects_discovered"] == len(_SOBJECTS)

    for entry in summary["scanned"]:
        detail = container.scan_service.get_scan_detail(entry["scan_id"])
        assert detail["status"] == "completed"


def test_scan_org_budget_stops_remaining_objects(tmp_path: Path):
    client, session = _client_and_session(_make_org_handler())
    container = build_container(data_dir=tmp_path, root=harness.ROOT)

    summary = asyncio.run(
        container.orchestrator_service.scan_org(client=client, session=session, api_budget=3)
    )

    assert len(summary["scanned"]) == 1
    assert summary["scanned"][0]["object"] == "Big__c"
    exhausted = [s for s in summary["skipped"] if s["reason"] == "api budget exhausted"]
    assert len(exhausted) == 3


def test_scan_org_include_filter_limits_to_named_objects(tmp_path: Path):
    client, session = _client_and_session(_make_org_handler())
    container = build_container(data_dir=tmp_path, root=harness.ROOT)

    summary = asyncio.run(
        container.orchestrator_service.scan_org(client=client, session=session, include=["Small__c"])
    )

    assert [s["object"] for s in summary["scanned"]] == ["Small__c"]


def test_bulk_incremental_ledgers_ids_and_advances_watermark(tmp_path: Path):
    describe = {
        "name": "Account",
        "fields": [
            {"name": "Id", "type": "id", "queryable": True, "nillable": False},
            {"name": "Name", "type": "string", "queryable": True, "nillable": False},
        ],
    }
    session = SalesforceSession("https://bulk-inc-test.my.salesforce.com", "FAKE_TOKEN", "v66.0")

    def initial_handler(request: httpx.Request) -> httpx.Response:
        p = request.url.path
        method = request.method.upper()
        if p.endswith("/sobjects/Account/describe"):
            return httpx.Response(200, json=describe)
        if p.endswith("/jobs/query") and method == "POST":
            return httpx.Response(200, json={"id": "750INI", "state": "UploadComplete"})
        if p.endswith("/jobs/query/750INI") and "/results" not in p:
            return httpx.Response(200, json={"state": "JobComplete"})
        if p.endswith("/jobs/query/750INI/results"):
            return httpx.Response(
                200, text="Id,Name\r\n001,Acme\r\n002,Beta\r\n", headers={"Sforce-Locator": "null"}
            )
        return httpx.Response(404, json={})

    client = SalesforceClient(session, transport=httpx.MockTransport(initial_handler))
    src = SalesforceBulkSource(
        client=client, session=session, object_name="Account",
        max_records=100_000, poll_interval=0, max_polls=5,
    )

    container = build_container(data_dir=tmp_path, root=harness.ROOT)
    first = asyncio.run(container.sync_service.incremental_scan(src))
    assert first["status"] == "completed"

    sync_repo = SyncRepository()
    with container.database.connect() as conn:
        assert sync_repo.ledger_active_ids(conn, "Account", BULK_SOURCE_KEY) == {"001", "002"}
        watermark_before = sync_repo.get_watermark(conn, "Account", BULK_SOURCE_KEY)
    assert watermark_before is not None

    captured_queries: list[str] = []

    def second_handler(request: httpx.Request) -> httpx.Response:
        p = request.url.path
        method = request.method.upper()
        if p.endswith("/sobjects/Account/describe"):
            return httpx.Response(200, json=describe)
        if p.endswith("/jobs/query") and method == "POST":
            body = json.loads(request.content.decode("utf-8"))
            captured_queries.append(body["query"])
            assert "WHERE SystemModstamp >=" in body["query"]
            return httpx.Response(200, json={"id": "750SEC", "state": "UploadComplete"})
        if p.endswith("/jobs/query/750SEC") and "/results" not in p:
            return httpx.Response(200, json={"state": "JobComplete"})
        if p.endswith("/jobs/query/750SEC/results"):
            return httpx.Response(
                200, text="Id,Name\r\n003,Gamma\r\n", headers={"Sforce-Locator": "null"}
            )
        return httpx.Response(404, json={})

    client2 = SalesforceClient(session, transport=httpx.MockTransport(second_handler))
    src2 = SalesforceBulkSource(
        client=client2, session=session, object_name="Account",
        max_records=100_000, poll_interval=0, max_polls=5,
    )
    second = asyncio.run(container.sync_service.incremental_scan(src2))
    assert second["status"] == "completed"
    assert len(captured_queries) == 1

    audit_repo = AuditRepository()
    with container.database.connect() as conn:
        assert sync_repo.ledger_active_ids(conn, "Account", BULK_SOURCE_KEY) == {"001", "002", "003"}
        watermark_after = sync_repo.get_watermark(conn, "Account", BULK_SOURCE_KEY)
        audits = [row for row in audit_repo.list(conn, limit=20) if row["event"] == "incremental_scan"]

    assert watermark_after is not None
    assert watermark_after >= watermark_before
    assert audits[0]["detail"]["mode"] == "incremental-bulk"


def test_org_scan_endpoint_without_credentials_returns_409(tmp_path: Path, monkeypatch):
    for var in ("SF_INSTANCE_URL", "SF_ACCESS_TOKEN", "SF_CLIENT_ID", "SF_CLIENT_SECRET"):
        monkeypatch.delenv(var, raising=False)
    with harness.api_client(tmp_path) as client:
        resp = client.post("/api/scans/salesforce/org", json={})
    assert resp.status_code == 409
