from __future__ import annotations

"""Regression test for a real-org bug found in live testing (2026-07-04):

Real Salesforce field-level describe metadata has NO "queryable" key — that flag exists only at
the object/sobject level. Our SalesforceObjectSource/SalesforceBulkSource used to filter selectable
fields by `field.get("queryable")`, which is always falsy on real describe responses, so every
live scan failed with "cannot query Id". Every prior mock test fixture (incorrectly) included a
field-level "queryable": True key, which hid this bug. This file's fixtures deliberately OMIT that
key, matching real Salesforce shape, to make sure the bug cannot silently return.
"""

import asyncio

import httpx

from app.ingestion.salesforce_bulk_source import SalesforceBulkSource
from app.ingestion.salesforce_source import SalesforceObjectSource
from app.salesforce.client import SalesforceClient
from app.salesforce.session import SalesforceSession


def _real_shaped_describe(object_name: str = "Account", queryable: bool = True) -> dict:
    """A describe payload shaped like real Salesforce: object-level queryable, no field-level key."""
    return {
        "name": object_name,
        "queryable": queryable,
        "fields": [
            {"name": "Id", "type": "id", "nillable": False},
            {"name": "Name", "type": "string", "nillable": False},
            {
                "name": "Type",
                "type": "picklist",
                "nillable": True,
                "picklistValues": [
                    {"value": "Customer", "active": True},
                    {"value": "Partner", "active": True},
                ],
            },
            {"name": "Description", "type": "textarea", "nillable": True},
        ],
    }


def _mock_source(describe: dict, query_records: list[dict]) -> SalesforceObjectSource:
    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith(f"/sobjects/{describe['name']}/describe"):
            return httpx.Response(200, json=describe)
        if path.endswith("/query"):
            return httpx.Response(200, json={"records": query_records})
        return httpx.Response(200, json={})

    session = SalesforceSession("https://real-shape.my.salesforce.com", "FAKE", "v66.0")
    client = SalesforceClient(session, transport=httpx.MockTransport(handler))
    return SalesforceObjectSource(client=client, session=session, object_name=describe["name"])


def test_field_selection_works_without_field_level_queryable_key():
    """The core bug: Id must be selected even though no field has a 'queryable' key."""
    describe = _real_shaped_describe()
    records = [{"Id": "001A", "Name": "Acme", "Type": "Customer"}]
    src = _mock_source(describe, records)
    ds = asyncio.run(src.load())
    assert "Id" in ds.fields
    assert "Name" in ds.fields
    assert "Type" in ds.fields
    assert "Description" not in ds.fields  # textarea excluded by type, not by queryable
    assert len(ds.records) == 1


def test_object_level_not_queryable_raises_clean_error():
    """When the OBJECT itself is not queryable (the real flag), fail with a clear 422."""
    from app.ingestion.base import IngestionError

    describe = _real_shaped_describe(queryable=False)
    src = _mock_source(describe, [])
    try:
        asyncio.run(src.load())
        assert False, "expected IngestionError"
    except IngestionError as exc:
        assert exc.status_code == 422
        assert "not queryable" in str(exc)


def test_bulk_source_field_selection_without_field_level_queryable_key():
    """Same regression, for the Bulk API 2.0 source."""
    describe = _real_shaped_describe()

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("/sobjects/Account/describe"):
            return httpx.Response(200, json=describe)
        if path.endswith("/jobs/query") and request.method == "POST":
            return httpx.Response(200, json={"id": "750AA", "state": "UploadComplete"})
        if path.endswith("/jobs/query/750AA"):
            return httpx.Response(200, json={"state": "JobComplete"})
        if path.endswith("/jobs/query/750AA/results"):
            return httpx.Response(
                200,
                text="Id,Name,Type\n001A,Acme,Customer\n",
                headers={"Sforce-Locator": "null"},
            )
        return httpx.Response(200, json={})

    session = SalesforceSession("https://real-shape.my.salesforce.com", "FAKE", "v66.0")
    client = SalesforceClient(session, transport=httpx.MockTransport(handler))
    src = SalesforceBulkSource(client=client, session=session, object_name="Account", poll_interval=0)
    asyncio.run(src._prepare())
    assert "Id" in src.fieldnames
    assert "Name" in src.fieldnames
