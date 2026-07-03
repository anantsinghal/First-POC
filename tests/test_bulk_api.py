from __future__ import annotations

import asyncio

import httpx
import pytest

from app.api.container import build_container as build_c
from app.ingestion.base import IngestionError
from app.ingestion.salesforce_bulk_source import SalesforceBulkSource
from app.salesforce import SalesforceClient, SalesforceError, SalesforceSession
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
    """Stateful handler for describe, job create, status (InProgress then JobComplete), results pages."""
    status_count = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        p = request.url.path
        qs = str(request.url.query or "")
        method = request.method.upper()
        if p.endswith("/sobjects/Account/describe"):
            return httpx.Response(200, json=_make_account_describe())
        if p.endswith("/jobs/query") and method == "POST":
            return httpx.Response(200, json={"id": "750XX", "state": "UploadComplete"})
        if p.endswith("/jobs/query/750XX") and "/results" not in p:
            status_count["n"] += 1
            if status_count["n"] <= 1:
                return httpx.Response(200, json={"state": "InProgress"})
            return httpx.Response(200, json={"state": "JobComplete"})
        if p.endswith("/jobs/query/750XX/results"):
            if "locator=LOC2" in qs:
                return httpx.Response(200, text=PAGE2, headers={"Sforce-Locator": "null"})
            return httpx.Response(200, text=PAGE1, headers={"Sforce-Locator": "LOC2"})
        return httpx.Response(200, json={})

    return handler, status_count


def test_bulk_client_units():
    """Client: create_job -> id; status InProgress->JobComplete; results yields 2 pages."""
    handler, _ = _make_stateful_handler()
    session = SalesforceSession("https://example.my.salesforce.com", "FAKE")
    client = SalesforceClient(session, transport=httpx.MockTransport(handler))
    job_id = asyncio.run(client.bulk_create_query_job("SELECT Id, Name, Type FROM Account"))
    assert job_id == "750XX"
    s1 = asyncio.run(client.bulk_job_status("750XX"))
    assert s1 == "InProgress"
    s2 = asyncio.run(client.bulk_job_status("750XX"))
    assert s2 == "JobComplete"
    pages: list[str] = []
    async def collect_pages():
        async for pg in client.bulk_job_results_pages("750XX", max_records=100):
            pages.append(pg)
    asyncio.run(collect_pages())
    assert len(pages) == 2
    assert "Frozen" in pages[0]
    assert "Sforce" not in pages[0]  # content only
    assert "Delta" in pages[1]


def test_bulk_source_batches(tmp_path):
    """Source aiter_batches: 5 records, batches <=2, schema has picklist, truncated=False."""
    handler, _ = _make_stateful_handler()
    session = SalesforceSession("https://example.my.salesforce.com", "FAKE")
    client = SalesforceClient(session, transport=httpx.MockTransport(handler))
    src = SalesforceBulkSource(
        client=client,
        session=session,
        object_name="Account",
        max_records=100000,
        poll_interval=0,
        max_polls=10,
        batch_size=5000,
    )
    batches: list[list] = []
    async def collect():
        async for b in src.aiter_batches(batch_size=2):
            batches.append(b)
    asyncio.run(collect())
    all_recs = [r for bb in batches for r in bb]
    assert len(all_recs) == 5
    assert all(len(bb) <= 2 for bb in batches)
    assert src.fieldnames and src.fieldnames[0] == "Id"
    assert src.schema is not None
    pf = src.schema.fields.get("Type")
    assert pf is not None and pf.picklist_values is not None and "Customer" in pf.picklist_values
    assert src.truncated is False


def test_bulk_stream_scan_end_to_end(tmp_path):
    """End-to-end stream_scan: completed, findings picklist_invalid + required_missing, audit scan_streamed."""
    handler, _ = _make_stateful_handler()
    session = SalesforceSession("https://example.my.salesforce.com", "FAKE")
    client = SalesforceClient(session, transport=httpx.MockTransport(handler))
    src = SalesforceBulkSource(
        client=client,
        session=session,
        object_name="Account",
        max_records=100000,
        poll_interval=0,
        max_polls=10,
    )
    container = build_c(data_dir=tmp_path, root=harness.ROOT)
    container.database.setup()
    scan = asyncio.run(container.scan_service.stream_scan(src))
    assert scan["status"] == "completed"
    findings = container.finding_service.list_findings(scan_id=scan["id"])
    ftypes = {f["finding_type"] for f in findings}
    assert "picklist_invalid" in ftypes
    assert "required_missing" in ftypes
    audits = container.audit_service.list(limit=20)
    streamed = [a for a in audits if a.get("event") == "scan_streamed" and a.get("scan_id") == scan["id"]]
    assert streamed, "missing scan_streamed audit"


def test_bulk_failed_state_raises():
    """Failed state with errorMessage raises cleanly (SalesforceError surfaces)."""
    def fail_handler(request: httpx.Request) -> httpx.Response:
        p = request.url.path
        method = request.method.upper()
        if p.endswith("/sobjects/Account/describe"):
            return httpx.Response(200, json=_make_account_describe())
        if p.endswith("/jobs/query") and method == "POST":
            return httpx.Response(200, json={"id": "750XX", "state": "UploadComplete"})
        if p.endswith("/jobs/query/750XX") and "/results" not in p:
            return httpx.Response(200, json={"state": "Failed", "errorMessage": "Simulated bulk failure"})
        return httpx.Response(200, json={})

    session = SalesforceSession("https://example.my.salesforce.com", "FAKE")
    client = SalesforceClient(session, transport=httpx.MockTransport(fail_handler))
    src = SalesforceBulkSource(
        client=client,
        session=session,
        object_name="Account",
        max_records=10,
        poll_interval=0,
        max_polls=5,
    )
    with pytest.raises((IngestionError, SalesforceError)) as exc:
        asyncio.run(src.load())
    # error message should contain the sf error
    assert "Simulated" in str(exc.value) or "Failed" in str(exc.value)


def test_bulk_truncation_record_cap(tmp_path):
    """max_records=3 -> truncated=True -> coverage partial with 'record cap'."""
    handler, _ = _make_stateful_handler()
    session = SalesforceSession("https://example.my.salesforce.com", "FAKE")
    client = SalesforceClient(session, transport=httpx.MockTransport(handler))
    src = SalesforceBulkSource(
        client=client,
        session=session,
        object_name="Account",
        max_records=3,
        poll_interval=0,
        max_polls=10,
    )
    container = build_c(data_dir=tmp_path, root=harness.ROOT)
    container.database.setup()
    scan = asyncio.run(container.scan_service.stream_scan(src))
    # via source attr or dataset in stream
    assert getattr(src, "truncated", False) is True
    assert scan["coverage"]["grade"] == "partial"
    assert any("record cap" in (s or "") for s in scan["coverage"].get("suppressed_checks", []))
