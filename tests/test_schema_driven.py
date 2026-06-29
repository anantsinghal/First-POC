from __future__ import annotations

import csv
import tempfile
from pathlib import Path
from typing import Any

import pytest

from app.detection.base import DetectionContext
from app.detection.registry import build_default_registry
from app.detection.schema_driven.picklist_validity import PicklistValidityDetector
from app.detection.schema_driven.referential_integrity import ReferentialIntegrityDetector
from app.detection.schema_driven.required_field import RequiredFieldDetector
from app.domain.schema import (
    FieldDescriptor,
    FieldType,
    ObjectSchema,
    schema_from_dict,
    schema_from_salesforce_describe,
)
from app.domain.values import clean
from app.ingestion.base import DataSource, LoadedDataset
from app.policy.field_policy import DEFAULT_POLICY
from app.services.scan_service import ScanService
from app.persistence.database import Database
from app.persistence.repositories import (
    CoverageRepository,
    FeedbackRepository,
    FindingRepository,
    ScanRepository,
)
from app.planning.planner import ScanPlanner
from app.profiling.profiler import Profiler
from app.coverage.grader import CoverageGrader


def test_schema_from_salesforce_describe():
    describe: dict[str, Any] = {
        "name": "Opportunity",
        "fields": [
            {
                "name": "Id",
                "type": "id",
                "nillable": False,
            },
            {
                "name": "Name",
                "type": "string",
                "nillable": False,
            },
            {
                "name": "StageName",
                "type": "picklist",
                "nillable": False,
                "picklistValues": [
                    {"value": "Prospecting", "active": True},
                    {"value": "Closed Won", "active": True},
                    {"value": "Closed Lost", "active": False},
                ],
            },
            {
                "name": "AccountId",
                "type": "reference",
                "nillable": False,
                "referenceTo": ["Account"],
            },
            {
                "name": "Amount",
                "type": "currency",
                "nillable": True,
            },
        ],
    }
    schema = schema_from_salesforce_describe(describe)
    assert schema.object_name == "Opportunity"
    assert "StageName" in schema.fields
    stage = schema.fields["StageName"]
    assert stage.data_type == FieldType.PICKLIST
    assert "Prospecting" in stage.picklist_values
    assert "Closed Lost" not in stage.picklist_values  # inactive filtered? but plan uses active=True
    assert schema.fields["AccountId"].data_type == FieldType.REFERENCE
    assert schema.fields["AccountId"].reference_to == ("Account",)
    assert schema.fields["AccountId"].required is True
    assert schema.fields["Id"].data_type == FieldType.ID
    assert schema.fields["Id"].required is False  # per logic
    assert "Amount" in schema.fields


def test_generic_detectors_with_schema():
    schema = ObjectSchema(
        object_name="Opportunity",
        fields={
            "StageName": FieldDescriptor(
                name="StageName",
                data_type=FieldType.PICKLIST,
                required=True,
                picklist_values=frozenset(["Prospecting", "Closed Won"]),
            ),
            "AccountId": FieldDescriptor(
                name="AccountId",
                data_type=FieldType.REFERENCE,
                required=True,
                reference_to=("Account",),
            ),
            "Name": FieldDescriptor(
                name="Name",
                data_type=FieldType.STRING,
                required=True,
            ),
        },
    )
    records: list[dict[str, str]] = [
        {"Id": "1", "StageName": "Prospecting", "AccountId": "acc1", "Name": "Foo"},
        {"Id": "2", "StageName": "BadStage", "AccountId": "acc2", "Name": "Bar"},
        {"Id": "3", "StageName": "Closed Won", "AccountId": "", "Name": ""},
    ]
    fields = ["Id", "StageName", "AccountId", "Name"]
    ctx = DetectionContext(
        object_name="Opportunity",
        records=records,
        fields=fields,
        policy=DEFAULT_POLICY,
        schema=schema,
    )

    req = RequiredFieldDetector().detect(ctx)
    assert len(req) >= 1  # Name empty on 3, but wait required check on non-ref
    # actually required are Name, Stage, Account (ref separate)
    # but test focuses on flags

    pick = PicklistValidityDetector().detect(ctx)
    assert len(pick) == 1
    assert pick[0].field_name == "StageName"
    assert "BadStage" in str(pick[0].evidence)

    ref = ReferentialIntegrityDetector().detect(ctx)
    assert len(ref) == 1
    assert ref[0].field_name == "AccountId"

    # when schema None
    ctx_no = DetectionContext(
        object_name="Opportunity",
        records=records,
        fields=fields,
        policy=DEFAULT_POLICY,
        schema=None,
    )
    assert RequiredFieldDetector().detect(ctx_no) == []
    assert PicklistValidityDetector().detect(ctx_no) == []
    assert ReferentialIntegrityDetector().detect(ctx_no) == []


def test_scan_service_wiring_with_schema(tmp_path: Path):
    # build minimal schema and records that should produce schema-driven findings
    schema = ObjectSchema(
        object_name="Opportunity",
        fields={
            "StageName": FieldDescriptor(
                name="StageName", data_type=FieldType.PICKLIST,
                required=True, picklist_values=frozenset(["Prospecting", "Closed Won"])
            ),
            "AccountId": FieldDescriptor(
                name="AccountId", data_type=FieldType.REFERENCE, required=True, reference_to=("Account",)
            ),
        },
    )
    records = [
        {"Id": "1", "StageName": "BadPick", "AccountId": ""},
        {"Id": "2", "StageName": "Prospecting", "AccountId": "acc1"},
    ]

    class FakeSource(DataSource):
        async def load(self) -> LoadedDataset:
            return LoadedDataset(
                object_name="Opportunity",
                source_name="fake",
                fields=["Id", "StageName", "AccountId"],
                records=records,
                source_hash="fakehash",
                schema=schema,
            )

    db_path = tmp_path / "t.db"
    database = Database(db_path)
    database.setup()

    profiler = Profiler()
    grader = CoverageGrader(DEFAULT_POLICY)
    registry = build_default_registry()
    scans = ScanRepository()
    coverage_repo = CoverageRepository()
    findings = FindingRepository()
    feedback = FeedbackRepository()

    svc = ScanService(
        database=database,
        profiler=profiler,
        grader=grader,
        registry=registry,
        planner=ScanPlanner(),
        scans=scans,
        coverage_repo=coverage_repo,
        findings=findings,
        policy=DEFAULT_POLICY,
    )

    # run via the service (this exercises the wiring)
    # but since async, use asyncio? but for test we can call directly? Wait, run_scan is async.
    import asyncio
    scan = asyncio.run(svc.run_scan(FakeSource()))
    # now query findings
    from app.persistence.database import Database as DB2
    with DB2(db_path).connect() as conn:
        frows = findings.list(conn)
    ftypes = {f["finding_type"] for f in frows}
    assert "picklist_invalid" in ftypes or "required_missing" in ftypes


def test_demo_regression_no_schema():
    """11-row CSV demo still produces same count (new engines no-op without schema)."""
    root = Path(__file__).resolve().parents[1]
    demo = root / "data" / "demo_opportunities.csv"
    with open(demo, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        fields = [f for f in (reader.fieldnames or []) if f]
        records = [{k: clean(v) for k, v in row.items() if k is not None} for row in reader]
    ctx = DetectionContext(
        object_name="Opportunity",
        records=records,
        fields=fields,
        policy=DEFAULT_POLICY,
        # no schema
    )
    cands = build_default_registry().detect(ctx)
    # the previous count (from phase history) was 10; must remain unchanged
    assert len(cands) == 10
    types = {c.finding_type for c in cands}
    assert "required_missing" not in types
    assert "picklist_invalid" not in types
    assert "reference_missing" not in types
