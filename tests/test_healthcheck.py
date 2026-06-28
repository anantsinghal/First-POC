from __future__ import annotations

import asyncio
from pathlib import Path

from app.detection.base import DetectionContext
from app.ingestion.salesforce_source import SalesforceObjectSource

from tests import harness


def test_system_healthcheck(tmp_path: Path):
    """Single authoritative end-to-end health gate using the harness."""

    # CSV demo (10 findings)
    recs, fields = harness.demo_records()
    f = harness.detect_all(harness.context("Opportunity", recs, fields=fields))
    assert len(f) == 10
    harness.assert_healthy(f)

    # Rich schema (all families) - 60 record fixture
    ctx = harness.rich_opportunity_context()
    f = harness.detect_all(ctx)
    types = harness.type_counts(f)
    expected = {
        "numeric_outlier",
        "robust_outlier",
        "rare_combination",
        "pattern_break",
        "ml_anomaly",
        "required_missing",
        "picklist_invalid",
        "reference_missing",
        "invalid_format",
        "orphan_reference",
        "missing_contract_closed_won",
    }
    assert expected <= set(types.keys())
    harness.assert_healthy(f)

    # Multi-object (3 missing_children)
    parents = [{"Id": f"acc{i:03d}", "Name": f"Account {i:03d}"} for i in range(20)]
    contacts = [
        {"Id": f"con{i:03d}", "AccountId": f"acc{i:03d}", "Name": f"Contact {i:03d}"}
        for i in range(17)
    ]
    ctx = harness.context(
        "Account", parents, related_records={"Contact": contacts}
    )
    f = harness.detect_all(ctx)
    assert harness.type_counts(f).get("missing_children", 0) == 3
    harness.assert_healthy(f)

    # Salesforce mock (picklist_invalid + invalid_format + required_missing)
    describe = {
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
    query_records = [
        {
            "Id": "001001",
            "Name": "Acme",
            "Type": "Customer",
            "Website": "https://ex.com",
            "attributes": {},
        },
        {
            "Id": "001002",
            "Name": "Globex",
            "Type": "Frozen",
            "Website": "nope",
            "attributes": {},
        },
        {
            "Id": "001003",
            "Name": "",
            "Type": "Partner",
            "Website": "https://p.com",
            "attributes": {},
        },
    ]
    src = harness.salesforce_source(
        "Account", describe=describe, query_records=query_records
    )
    ds = asyncio.run(src.load())
    f = harness.detect_all(
        harness.context(
            "Account",
            ds.records,
            fields=ds.fields,
            schema=ds.schema,
            related_ids=ds.related_ids,
        )
    )
    got = set(harness.type_counts(f).keys())
    assert {"picklist_invalid", "invalid_format", "required_missing"} <= got

    # API smoke (via harness client)
    with harness.api_client(tmp_path) as client:
        # health
        r = client.get("/api/health")
        assert r.status_code == 200
        assert r.json().get("status") == "ok"

        # demo scan
        r = client.post("/api/scans/demo")
        assert r.status_code == 200
        scan = r.json()
        assert scan.get("finding_count") == 10

        # findings
        r = client.get(f"/api/findings?scan_id={scan['id']}")
        assert r.status_code == 200
        assert len(r.json()) > 0

        # dashboard
        r = client.get("/api/dashboard")
        assert r.status_code == 200

        # audit has scan_completed
        r = client.get("/api/audit?limit=5")
        assert r.status_code == 200
        events = [a.get("event") for a in r.json()]
        assert "scan_completed" in events
