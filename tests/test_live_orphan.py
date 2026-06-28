from __future__ import annotations

import asyncio

import httpx

from app.detection.base import DetectionContext
from app.detection.registry import build_default_registry
from app.ingestion.salesforce_source import SalesforceObjectSource
from app.policy.field_policy import DEFAULT_POLICY
from app.salesforce.client import SalesforceClient
from app.salesforce.session import SalesforceSession


def test_live_orphan_resolution_with_mocks():
    """Mocked live Salesforce scan resolves Account parents; flags exactly the dangling AccountId as orphan_reference.
    resolve_references=False variant produces no related_ids and no orphan.
    """
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/sobjects/Opportunity/describe"):
            return httpx.Response(
                200,
                json={
                    "name": "Opportunity",
                    "fields": [
                        {"name": "Id", "type": "id", "queryable": True, "nillable": False},
                        {"name": "Name", "type": "string", "queryable": True, "nillable": False},
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
                        {
                            "name": "AccountId",
                            "type": "reference",
                            "queryable": True,
                            "nillable": True,
                            "referenceTo": ["Account"],
                        },
                        {"name": "Amount", "type": "double", "queryable": True, "nillable": True},
                    ],
                },
            )
        if request.url.path.endswith("/query"):
            q = request.url.params.get("q", "") or ""
            if "Opportunity" in q:
                # 4 opps: 3 valid AccountIds + 1 dangling
                return httpx.Response(
                    200,
                    json={
                        "records": [
                            {
                                "Id": "006001",
                                "Name": "Good1",
                                "StageName": "Prospecting",
                                "AccountId": "001AAA",
                                "Amount": "10000",
                                "attributes": {},
                            },
                            {
                                "Id": "006002",
                                "Name": "Good2",
                                "StageName": "Closed Won",
                                "AccountId": "001BBB",
                                "Amount": "20000",
                                "attributes": {},
                            },
                            {
                                "Id": "006003",
                                "Name": "Good3",
                                "StageName": "Prospecting",
                                "AccountId": "001CCC",
                                "Amount": "30000",
                                "attributes": {},
                            },
                            {
                                "Id": "006004",
                                "Name": "Dangling",
                                "StageName": "Prospecting",
                                "AccountId": "001MISSING",
                                "Amount": "40000",
                                "attributes": {},
                            },
                        ]
                    },
                )
            if "Account" in q:
                # existence check: echo only the 3 valid Ids (exclude 001MISSING)
                return httpx.Response(
                    200,
                    json={
                        "records": [
                            {"Id": "001AAA", "attributes": {}},
                            {"Id": "001BBB", "attributes": {}},
                            {"Id": "001CCC", "attributes": {}},
                        ]
                    },
                )
        return httpx.Response(200, json={})

    session = SalesforceSession("https://example.my.salesforce.com", "token")
    client = SalesforceClient(session, transport=httpx.MockTransport(handler))

    # default: resolve_references=True
    src = SalesforceObjectSource(client=client, session=session, object_name="Opportunity")
    ds = asyncio.run(src.load())

    assert ds.related_ids is not None
    assert "Account" in ds.related_ids
    assert ds.related_ids["Account"] == {"001AAA", "001BBB", "001CCC"}
    assert "001MISSING" not in ds.related_ids["Account"]

    # detection with live related_ids should yield exactly one orphan on the dangling one
    findings = build_default_registry().detect(
        DetectionContext(
            "Opportunity",
            ds.records,
            ds.fields,
            DEFAULT_POLICY,
            schema=ds.schema,
            related_ids=ds.related_ids,
        )
    )
    orphan_findings = [f for f in findings if f.finding_type == "orphan_reference"]
    assert len(orphan_findings) == 1
    assert orphan_findings[0].record_id in ("006004",) or "001MISSING" in str(orphan_findings[0].evidence)
    # confirm on the specific dangling
    dangling = [f for f in orphan_findings if f.evidence.get("observed_value") == "001MISSING"]
    assert len(dangling) == 1

    # resolve_references=False variant: no resolution, no orphan
    src_no = SalesforceObjectSource(
        client=client, session=session, object_name="Opportunity", resolve_references=False
    )
    ds_no = asyncio.run(src_no.load())
    assert ds_no.related_ids is None or not ds_no.related_ids

    findings_no = build_default_registry().detect(
        DetectionContext(
            "Opportunity",
            ds_no.records,
            ds_no.fields,
            DEFAULT_POLICY,
            schema=ds_no.schema,
            related_ids=ds_no.related_ids or {},
        )
    )
    orphan_findings_no = [f for f in findings_no if f.finding_type == "orphan_reference"]
    assert len(orphan_findings_no) == 0
