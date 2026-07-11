"""Phase 65 — incremental delta statistical validity.

Delta scans must run only tier-0 (contract/rule) detectors + orphan_reference. Computing
IQR/MAD/rare-combination/ML baselines from a changes-only population yields invalid findings.
Reconcile (full pull) still uses the full registry so true population baselines remain valid.
"""
from __future__ import annotations

import asyncio
from pathlib import Path

import httpx

from app.api.container import build_container
from app.ingestion.salesforce_source import SalesforceObjectSource
from app.persistence.repositories import AuditRepository, FindingRepository
from app.salesforce.client import SalesforceClient
from app.salesforce.session import SalesforceSession
from tests import harness

STATISTICAL_TYPES = frozenset(
    {"numeric_outlier", "robust_outlier", "ml_anomaly", "rare_combination"}
)


def _describe_payload() -> dict:
    return {
        "name": "Opportunity",
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
            {"name": "Amount", "type": "double", "queryable": True, "nillable": True},
        ],
    }


def _source(transport: httpx.MockTransport, *, max_records: int = 500):
    session = SalesforceSession("https://delta-test.my.salesforce.com", "FAKE_TOKEN", "v66.0")
    client = SalesforceClient(session, transport=transport)
    return SalesforceObjectSource(
        client=client,
        session=session,
        object_name="Opportunity",
        max_records=max_records,
    )


def _delta_records() -> list[dict]:
    """60 records with Amounts near 100k and one 9,000,000 outlier; one bad picklist."""
    recs = [
        {
            "Id": f"006{i:015d}",
            "Name": f"Deal {i}",
            "Status": "A" if i % 2 == 0 else "B",
            "Amount": str(100_000 + (i % 10) * 1_000),
            "attributes": {},
        }
        for i in range(60)
    ]
    # Extreme Amount: a full-registry scan on this population WOULD flag numeric_outlier.
    recs[10]["Amount"] = "9000000"
    # Bad picklist: tier-0 must still catch this on a delta.
    recs[5]["Status"] = "Frozen"
    return recs


def test_delta_skips_statistical_detectors_but_catches_picklist(tmp_path: Path):
    """Incremental delta: no statistical findings on a 9M outlier; picklist still flagged.
    Reconcile on the same extreme value yields numeric_outlier (full registry)."""

    def initial_handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/sobjects/Opportunity/describe"):
            return httpx.Response(200, json=_describe_payload())
        if request.url.path.endswith("/query"):
            return httpx.Response(
                200,
                json={
                    "records": [
                        {
                            "Id": "006SEED000000001",
                            "Name": "Seed",
                            "Status": "A",
                            "Amount": "100000",
                            "attributes": {},
                        }
                    ]
                },
            )
        return httpx.Response(404, json={})

    container = build_container(data_dir=tmp_path, root=harness.ROOT)
    # Establish watermark so the next call is a true incremental (changes-only) scan.
    first = asyncio.run(
        container.sync_service.incremental_scan(_source(httpx.MockTransport(initial_handler)))
    )
    assert first["status"] == "completed"
    assert first["sync_mode"] == "initial"

    delta_recs = _delta_records()

    def delta_handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/sobjects/Opportunity/describe"):
            return httpx.Response(200, json=_describe_payload())
        if request.url.path.endswith("/query"):
            q = request.url.params.get("q", "")
            assert "WHERE SystemModstamp >=" in q
            return httpx.Response(200, json={"records": delta_recs})
        return httpx.Response(404, json={})

    delta_scan = asyncio.run(
        container.sync_service.incremental_scan(_source(httpx.MockTransport(delta_handler)))
    )

    assert delta_scan["status"] == "completed"
    assert delta_scan["sync_mode"] == "incremental"
    assert delta_scan["changed_records"] == 60
    assert "delta scan" in (delta_scan.get("notes") or "")

    finding_repo = FindingRepository()
    audit_repo = AuditRepository()
    with container.database.connect() as conn:
        findings = [
            row
            for row in finding_repo.list(conn, scan_id=delta_scan["id"])
        ]
        completed = [
            row
            for row in audit_repo.list(conn, limit=50)
            if row["event"] == "scan_completed" and row["scan_id"] == delta_scan["id"]
        ]

    ftypes = {row["finding_type"] for row in findings}
    assert STATISTICAL_TYPES.isdisjoint(ftypes), (
        f"delta scan must not emit statistical findings; got {ftypes & STATISTICAL_TYPES}"
    )
    assert "picklist_invalid" in ftypes, "tier-0 picklist check must still run on delta"

    assert completed, "missing scan_completed audit for delta scan"
    detail = completed[0]["detail"]
    assert detail.get("delta_scan") is True
    deferred = detail.get("deferred_detectors") or []
    for sid in STATISTICAL_TYPES:
        assert sid in deferred, f"{sid} must appear in deferred_detectors"

    # Reconcile = full pull, full registry — same extreme Amount MUST yield numeric_outlier.
    def reconcile_handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/sobjects/Opportunity/describe"):
            return httpx.Response(200, json=_describe_payload())
        if request.url.path.endswith("/query"):
            q = request.url.params.get("q", "")
            assert "WHERE SystemModstamp >=" not in q
            return httpx.Response(200, json={"records": delta_recs})
        return httpx.Response(404, json={})

    reconciled = asyncio.run(
        container.sync_service.reconcile(_source(httpx.MockTransport(reconcile_handler)))
    )
    assert reconciled["status"] == "completed"

    with container.database.connect() as conn:
        recon_findings = list(finding_repo.list(conn, scan_id=reconciled["id"]))
    recon_types = {row["finding_type"] for row in recon_findings}
    assert "numeric_outlier" in recon_types, (
        f"reconcile full registry must flag the 9M Amount; got {recon_types}"
    )
