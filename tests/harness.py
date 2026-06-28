"""Reusable testing harness for the Salesforce data quality scanner.

Builders, assertions, and helpers to make verification deterministic and compact.
Add a detector → add its expected type to the healthcheck families set + a focused test
using these helpers. If a change breaks the healthcheck, fix the logic; if the harness
can't express a case, extend the harness.
"""

from __future__ import annotations

import asyncio
import csv
import importlib
import io
import json
import os
from collections import Counter
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

import httpx
from fastapi.testclient import TestClient

from app.api.container import build_container
from app.detection.base import DetectionContext
from app.detection.registry import build_default_registry
from app.domain.models import Record
from app.domain.schema import ObjectSchema, schema_from_dict
from app.domain.values import clean
from app.ingestion.base import LoadedDataset
from app.ingestion.salesforce_source import SalesforceObjectSource
from app.policy.field_policy import DEFAULT_POLICY
from app.salesforce.client import SalesforceClient
from app.salesforce.session import SalesforceSession

ROOT = Path(__file__).resolve().parents[1]


# --- Builders ---

def demo_records() -> tuple[list[dict], list[str]]:
    """Load the demo CSV (utf-8-sig, cleaned)."""
    demo_path = ROOT / "data" / "demo_opportunities.csv"
    with open(demo_path, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        fields = [f for f in (reader.fieldnames or []) if f]
        records: list[dict] = [
            {k: clean(v) for k, v in row.items() if k is not None} for row in reader
        ]
    return records, fields


def csv_bytes(headers: list[str], rows: list[list[str]]) -> bytes:
    """Build CSV bytes from headers + rows."""
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(headers)
    w.writerows(rows)
    return (buf.getvalue()).encode("utf-8")


def make_schema(object_name: str, fields: list[dict]) -> ObjectSchema:
    """Thin wrapper for schema_from_dict."""
    return schema_from_dict({"object_name": object_name, "fields": fields})


def context(
    object_name: str,
    records: list[dict],
    *,
    fields: list[str] | None = None,
    schema: ObjectSchema | None = None,
    related_ids: dict[str, frozenset[str]] | None = None,
    related_records: dict[str, list[Record]] | None = None,
    policy=DEFAULT_POLICY,
) -> DetectionContext:
    """Build DetectionContext; fields default to first record's keys."""
    if fields is None:
        fields = list(records[0].keys()) if records else []
    return DetectionContext(
        object_name=object_name,
        records=records,
        fields=fields,
        policy=policy,
        schema=schema,
        related_ids=related_ids or {},
        related_records=related_records or {},
    )


def detect_all(ctx: DetectionContext) -> list:
    """Run the full default registry."""
    return build_default_registry().detect(ctx)


def type_counts(findings: list) -> dict[str, int]:
    """Counter of finding_type."""
    return dict(Counter(f.finding_type for f in findings))


def rich_opportunity_context() -> DetectionContext:
    """The 60-record schema'd fixture exercising all detector families (from test_integration)."""
    schema_data = {
        "object_name": "Opportunity",
        "fields": [
            {"name": "Id", "data_type": "id"},
            {
                "name": "Name",
                "data_type": "string",
                "required": True,
                "is_name_field": True,
            },
            {
                "name": "StageName",
                "data_type": "picklist",
                "picklist_values": [
                    "Prospecting",
                    "Qualification",
                    "Closed Won",
                    "Closed Lost",
                ],
            },
            {
                "name": "AccountId",
                "data_type": "reference",
                "required": True,
                "reference_to": ["Account"],
            },
            {"name": "OwnerEmail", "data_type": "email"},
            {"name": "Website", "data_type": "url"},
            {"name": "Amount", "data_type": "number"},
            {
                "name": "Contract__c",
                "data_type": "reference",
                "reference_to": ["Contract"],
            },
            {
                "name": "Segment__c",
                "data_type": "picklist",
                "picklist_values": ["Enterprise", "SMB"],
            },
            {"name": "CloseDate", "data_type": "date"},
        ],
    }
    schema = schema_from_dict(schema_data)
    fields = [field["name"] for field in schema_data["fields"]]
    valid_accts = {f"001ACC{i:03d}" for i in range(60)}
    recs = [
        {
            "Id": f"006OPP{i:03d}",
            "Name": f"Deal {i}",
            "StageName": "Closed Won" if i % 2 == 0 else "Prospecting",
            "AccountId": f"001ACC{i:03d}",
            "OwnerEmail": f"owner{i}@example.com",
            "Website": "https://example.com",
            "Amount": str(100000 + (i % 10) * 1000),
            "Contract__c": f"800CON{i:03d}" if i % 2 == 0 else "",
            "Segment__c": "Enterprise" if i % 3 else "SMB",
            "CloseDate": "2026-12-01",
        }
        for i in range(60)
    ]
    recs[5]["StageName"] = "Frozen"
    recs[6]["Name"] = ""
    recs[7]["OwnerEmail"] = "not-an-email"
    recs[8]["Website"] = "nope"
    recs[9]["AccountId"] = "001MISSING"
    recs[10]["Amount"] = "9000000"
    recs[11]["AccountId"] = ""
    recs[12]["Contract__c"] = ""
    return context(
        "Opportunity",
        recs,
        fields=fields,
        schema=schema,
        related_ids={"Account": frozenset(valid_accts)},
    )


# --- Assertions ---

def assert_finding_contract(f) -> None:
    """Basic contract for any Candidate/Finding."""
    assert f.severity.value in {"high", "medium", "low"}, f"bad severity: {f.severity}"
    assert 0 <= f.confidence <= 1, f"bad confidence: {f.confidence}"
    assert 0 <= f.priority <= 1, f"bad priority: {f.priority}"
    assert f.record_id, "empty record_id"
    assert isinstance(f.evidence, dict), "evidence not dict"
    assert "why" in f.evidence, "missing 'why' in evidence"
    json.dumps(f.evidence)  # must be serializable


def assert_healthy(findings: list) -> None:
    """Unique fingerprints + contract for each."""
    fps = [getattr(f, "fingerprint", None) or str(id(f)) for f in findings]
    assert len(fps) == len(set(fps)), "duplicate fingerprints"
    for f in findings:
        assert_finding_contract(f)


# --- Helpers ---

class FakeSource:
    """DataSource that returns a pre-built LoadedDataset."""

    def __init__(self, dataset: LoadedDataset):
        self._dataset = dataset

    async def load(self) -> LoadedDataset:
        return self._dataset


def run_scan(dataset: LoadedDataset, data_dir: Path) -> tuple[dict, list[dict]]:
    """Run a scan via the fully-wired container (no API). Return (scan_detail, findings)."""
    container = build_container(data_dir=data_dir, root=ROOT)
    container.database.setup()
    scan = asyncio.run(container.scan_service.run_scan(FakeSource(dataset)))
    findings = container.finding_service.list_findings(scan_id=scan["id"])
    return scan, findings


@contextmanager
def api_client(data_dir: Path) -> Iterator[TestClient]:
    """Context that sets SCANNER_DATA_DIR, reloads app.main, and yields a TestClient."""
    os.environ["SCANNER_DATA_DIR"] = str(data_dir)
    import app.main as main

    main = importlib.reload(main)
    with TestClient(main.app) as c:
        yield c


def salesforce_source(
    object_name: str,
    *,
    describe: dict,
    query_records: list[dict],
    parent_existing: dict[str, set[str]] | None = None,
    **kwargs,
) -> SalesforceObjectSource:
    """Build SalesforceObjectSource over a MockTransport.

    The transport answers:
    - describe
    - main SELECT ... FROM <object>
    - parent SELECT Id FROM <Parent> WHERE Id IN (...) by echoing matching ids from parent_existing.
    Uses a fake session. No real credentials ever used.
    """
    if parent_existing is None:
        parent_existing = {}

    def handler(request: httpx.Request) -> httpx.Response:
        p = getattr(request.url, "path", str(request.url))
        if p.endswith(f"/sobjects/{object_name}/describe"):
            return httpx.Response(200, json=describe)
        if p.endswith("/query"):
            q = request.url.params.get("q", "") or ""
            if f"FROM {object_name}" in q:
                return httpx.Response(200, json={"records": query_records})
            # parent existence probes
            for parent, valid_ids in parent_existing.items():
                if f"FROM {parent} WHERE Id IN" in q:
                    # extract ids from IN clause
                    import re

                    ids = re.findall(r"'([^']*)'", q)
                    recs = [{"Id": iid, "attributes": {}} for iid in ids if iid in valid_ids]
                    return httpx.Response(200, json={"records": recs})
            return httpx.Response(200, json={"records": []})
        return httpx.Response(200, json={})

    session = SalesforceSession(
        "https://harness.my.salesforce.com", "FAKE_TOKEN", "v66.0"
    )
    client = SalesforceClient(session, transport=httpx.MockTransport(handler))
    return SalesforceObjectSource(
        client=client, session=session, object_name=object_name, **kwargs
    )
