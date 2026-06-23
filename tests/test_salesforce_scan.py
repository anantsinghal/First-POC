from __future__ import annotations

import asyncio

import httpx

from app.detection.base import DetectionContext
from app.detection.registry import build_default_registry
from app.ingestion.salesforce_source import SalesforceObjectSource
from app.policy.field_policy import DEFAULT_POLICY
from app.salesforce import SalesforceClient, SalesforceSession


def test_salesforce_account_scan_uses_describe_fields_and_schema_detectors():
    """Mock the full Account path: describe-derived fields plus schema-driven findings."""
    queries: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/sobjects/Account/describe"):
            return httpx.Response(
                200,
                json={
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
                        {
                            "name": "Industry",
                            "type": "picklist",
                            "queryable": True,
                            "nillable": True,
                            "picklistValues": [{"value": "Tech", "active": True}],
                        },
                        {"name": "Description", "type": "textarea", "queryable": True, "nillable": True},
                    ],
                },
            )
        if request.url.path.endswith("/query"):
            queries.append(request.url.params.get("q", ""))
            return httpx.Response(
                200,
                json={
                    "records": [
                        {
                            "Id": "001001",
                            "Name": "Acme",
                            "Type": "Customer",
                            "Website": "https://acme.example",
                            "Industry": "Tech",
                            "attributes": {},
                        },
                        {
                            "Id": "001002",
                            "Name": "Globex",
                            "Type": "Frozen",
                            "Website": "not-a-url",
                            "Industry": "Tech",
                            "attributes": {},
                        },
                        {
                            "Id": "001003",
                            "Name": "",
                            "Type": "Partner",
                            "Website": "https://partner.example",
                            "Industry": "Tech",
                            "attributes": {},
                        },
                    ]
                },
            )
        return httpx.Response(404, json={})

    session = SalesforceSession("https://carhuborg.my.salesforce.com", "FAKE_TOKEN", "v66.0")
    client = SalesforceClient(session, transport=httpx.MockTransport(handler))
    source = SalesforceObjectSource(client=client, session=session, object_name="Account")

    ds = asyncio.run(source.load())

    assert ds.fields == ["Id", "Name", "Type", "Website", "Industry"]
    assert "Description" not in ds.fields
    assert queries == ["SELECT Id, Name, Type, Website, Industry FROM Account"]
    assert len(ds.records) == 3

    findings = build_default_registry().detect(
        DetectionContext(
            "Account",
            ds.records,
            ds.fields,
            DEFAULT_POLICY,
            schema=ds.schema,
        )
    )
    finding_types = {finding.finding_type for finding in findings}
    assert "picklist_invalid" in finding_types
    assert "invalid_format" in finding_types
    assert "required_missing" in finding_types
