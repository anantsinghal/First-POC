"""Phase 66: bulk orphan honesty + bounded existence resolution on the stream path."""
from __future__ import annotations

import asyncio

import httpx

from app.api.container import build_container as build_c
from app.ingestion.salesforce_bulk_source import SalesforceBulkSource
from app.salesforce import SalesforceClient, SalesforceSession
from tests import harness


def _make_opportunity_describe():
    return {
        "name": "Opportunity",
        "fields": [
            {"name": "Id", "type": "id", "queryable": True, "nillable": False},
            {"name": "Name", "type": "string", "queryable": True, "nillable": False},
            {
                "name": "AccountId",
                "type": "reference",
                "queryable": True,
                "nillable": True,
                "referenceTo": ["Account"],
            },
            {
                "name": "StageName",
                "type": "picklist",
                "queryable": True,
                "nillable": True,
                "picklistValues": [
                    {"value": "Prospecting", "active": True},
                    {"value": "Closed Won", "active": True},
                ],
            },
        ],
    }


# Three distinct AccountIds; existence probe omits 001MISSING (dangling).
ORPHAN_CSV = (
    "Id,Name,AccountId,StageName\r\n"
    "006001,Good1,001AAA,Prospecting\r\n"
    "006002,Good2,001BBB,Closed Won\r\n"
    "006003,Dangling,001MISSING,Prospecting\r\n"
)


def _make_bulk_orphan_handler(*, probe_fail: bool = False):
    """Bulk job + optional REST existence probe for Account parents."""
    status_count = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        p = request.url.path
        method = request.method.upper()
        if p.endswith("/sobjects/Opportunity/describe"):
            return httpx.Response(200, json=_make_opportunity_describe())
        if p.endswith("/jobs/query") and method == "POST":
            return httpx.Response(200, json={"id": "750ORPH", "state": "UploadComplete"})
        if p.endswith("/jobs/query/750ORPH") and "/results" not in p:
            status_count["n"] += 1
            if status_count["n"] <= 1:
                return httpx.Response(200, json={"state": "InProgress"})
            return httpx.Response(200, json={"state": "JobComplete"})
        if p.endswith("/jobs/query/750ORPH/results"):
            return httpx.Response(
                200, text=ORPHAN_CSV, headers={"Sforce-Locator": "null"}
            )
        if "/query" in p and method == "GET":
            q = request.url.params.get("q", "") or ""
            if "FROM Account" in q or "from Account" in q:
                if probe_fail:
                    return httpx.Response(400, json=[{"message": "probe denied"}])
                # Echo only the two valid parents; omit 001MISSING.
                return httpx.Response(
                    200,
                    json={
                        "records": [
                            {"Id": "001AAA", "attributes": {}},
                            {"Id": "001BBB", "attributes": {}},
                        ],
                        "done": True,
                    },
                )
            return httpx.Response(200, json={"records": [], "done": True})
        return httpx.Response(200, json={})

    return handler


def _stream_bulk_orphan(tmp_path, *, probe_fail: bool = False):
    handler = _make_bulk_orphan_handler(probe_fail=probe_fail)
    session = SalesforceSession("https://example.my.salesforce.com", "FAKE")
    client = SalesforceClient(session, transport=httpx.MockTransport(handler))
    src = SalesforceBulkSource(
        client=client,
        session=session,
        object_name="Opportunity",
        max_records=100000,
        poll_interval=0,
        max_polls=10,
        batch_size=5000,
    )
    container = build_c(data_dir=tmp_path, root=harness.ROOT)
    container.database.setup()
    scan = asyncio.run(container.scan_service.stream_scan(src))
    return container, scan


def test_bulk_orphan_dangling_id_caught(tmp_path):
    """Existence probe omits one parent → orphan_reference finding; deferred clean of orphan."""
    container, scan = _stream_bulk_orphan(tmp_path, probe_fail=False)
    assert scan["status"] == "completed"

    findings = container.finding_service.list_findings(scan_id=scan["id"])
    orphans = [f for f in findings if f["finding_type"] == "orphan_reference"]
    assert len(orphans) >= 1, f"expected orphan_reference findings, got types={[f['finding_type'] for f in findings]}"
    dangling = [
        f
        for f in orphans
        if (f.get("evidence") or {}).get("observed_value") == "001MISSING"
        or f.get("record_id") == "006003"
    ]
    assert dangling, f"dangling 001MISSING / 006003 not found in {orphans}"

    audits = container.audit_service.list(limit=20)
    streamed = [
        a
        for a in audits
        if a.get("event") == "scan_streamed" and a.get("scan_id") == scan["id"]
    ]
    assert streamed, "missing scan_streamed audit"
    deferred = streamed[0].get("detail", {}).get("deferred") or []
    assert "orphan_reference" not in deferred, (
        f"orphan_reference must not be deferred when resolution ran; deferred={deferred}"
    )


def test_bulk_orphan_probe_failure_honestly_deferred(tmp_path):
    """Existence probe HTTP 400 → no orphan findings; deferred lists orphan_reference."""
    container, scan = _stream_bulk_orphan(tmp_path, probe_fail=True)
    assert scan["status"] == "completed"

    findings = container.finding_service.list_findings(scan_id=scan["id"])
    orphans = [f for f in findings if f["finding_type"] == "orphan_reference"]
    assert orphans == [], f"expected no orphan findings on probe failure, got {orphans}"

    notes = scan.get("notes") or ""
    assert "orphan checks deferred" in notes

    audits = container.audit_service.list(limit=20)
    streamed = [
        a
        for a in audits
        if a.get("event") == "scan_streamed" and a.get("scan_id") == scan["id"]
    ]
    assert streamed, "missing scan_streamed audit"
    deferred = streamed[0].get("detail", {}).get("deferred") or []
    assert "orphan_reference" in deferred, (
        f"orphan_reference must be honestly deferred; deferred={deferred}"
    )
