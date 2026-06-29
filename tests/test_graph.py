from __future__ import annotations

import asyncio
import csv
from pathlib import Path

from app.detection.base import DetectionContext
from app.detection.graph.orphan_reference import OrphanReferenceDetector
from app.detection.registry import build_default_registry
from app.domain.schema import FieldDescriptor, FieldType, ObjectSchema
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


def test_orphan_cross_object():
    schema = ObjectSchema(
        object_name="Opportunity",
        fields={
            "AccountId": FieldDescriptor(
                name="AccountId",
                data_type=FieldType.REFERENCE,
                required=False,
                reference_to=("Account",),
            ),
        },
    )
    records: list[dict[str, str]] = [
        {"Id": "1", "AccountId": "acc1"},
        {"Id": "2", "AccountId": "acc2"},
        {"Id": "3", "AccountId": "badacc"},  # dangling
        {"Id": "4", "AccountId": ""},  # empty, not flagged
    ]
    related_ids = {"Account": frozenset({"acc1", "acc2"})}
    ctx = DetectionContext(
        object_name="Opportunity",
        records=records,
        fields=["Id", "AccountId"],
        policy=DEFAULT_POLICY,
        schema=schema,
        related_ids=related_ids,
    )
    cands = OrphanReferenceDetector().detect(ctx)
    assert len(cands) == 1
    assert cands[0].finding_type == "orphan_reference"
    assert cands[0].field_name == "AccountId"
    assert cands[0].evidence["observed_value"] == "badacc"


def test_orphan_self_reference():
    schema = ObjectSchema(
        object_name="Account",
        fields={
            "ParentId": FieldDescriptor(
                name="ParentId",
                data_type=FieldType.REFERENCE,
                required=False,
                reference_to=("Account",),
            ),
        },
    )
    records: list[dict[str, str]] = [
        {"Id": "acc1", "ParentId": "acc1"},
        {"Id": "acc2", "ParentId": "badparent"},
    ]
    ctx = DetectionContext(
        object_name="Account",
        records=records,
        fields=["Id", "ParentId"],
        policy=DEFAULT_POLICY,
        schema=schema,
        related_ids={},
    )
    cands = OrphanReferenceDetector().detect(ctx)
    assert len(cands) == 1
    assert cands[0].finding_type == "orphan_reference"
    assert cands[0].field_name == "ParentId"
    assert cands[0].evidence["observed_value"] == "badparent"


def test_orphan_noop_cases():
    ctx_no_schema = DetectionContext(
        object_name="Opportunity",
        records=[{"Id": "1", "AccountId": "bad"}],
        fields=["Id", "AccountId"],
        policy=DEFAULT_POLICY,
        schema=None,
    )
    assert OrphanReferenceDetector().detect(ctx_no_schema) == []

    schema = ObjectSchema(
        object_name="Opportunity",
        fields={
            "AccountId": FieldDescriptor(
                name="AccountId",
                data_type=FieldType.REFERENCE,
                required=False,
                reference_to=("Account",),
            ),
        },
    )
    ctx_no_related = DetectionContext(
        object_name="Opportunity",
        records=[{"Id": "1", "AccountId": "bad"}],
        fields=["Id", "AccountId"],
        policy=DEFAULT_POLICY,
        schema=schema,
        related_ids={},
    )
    # no own_ids for Account, no related_ids -> skip the field
    assert OrphanReferenceDetector().detect(ctx_no_related) == []


def test_graph_wiring(tmp_path: Path):
    schema = ObjectSchema(
        object_name="Opportunity",
        fields={
            "AccountId": FieldDescriptor(
                name="AccountId",
                data_type=FieldType.REFERENCE,
                required=False,
                reference_to=("Account",),
            ),
        },
    )
    records = [{"Id": "1", "AccountId": "badacc"}]
    related_ids = {"Account": frozenset({"acc1"})}

    class FakeSource(DataSource):
        async def load(self) -> LoadedDataset:
            return LoadedDataset(
                object_name="Opportunity",
                source_name="fake",
                fields=["Id", "AccountId"],
                records=records,
                source_hash="fake",
                schema=schema,
                related_ids=related_ids,
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
    scan = asyncio.run(svc.run_scan(FakeSource()))
    with Database(db_path).connect() as conn:
        frows = findings.list(conn)
    types = {f["finding_type"] for f in frows}
    assert "orphan_reference" in types


def test_demo_regression_no_orphan():
    """Demo via full registry yields same count, zero orphan_reference (no schema)."""
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
    )
    cands = build_default_registry().detect(ctx)
    assert len(cands) == 10  # unchanged
    assert sum(1 for c in cands if c.finding_type == "orphan_reference") == 0
