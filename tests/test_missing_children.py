from __future__ import annotations

import asyncio
import csv
from pathlib import Path

from app.coverage.grader import CoverageGrader
from app.detection.base import DetectionContext
from app.detection.graph.missing_children import MissingChildrenDetector
from app.detection.registry import build_default_registry
from app.domain.schema import ObjectSchema
from app.domain.values import clean
from app.ingestion.base import DataSource, LoadedDataset
from app.persistence.database import Database
from app.persistence.repositories import CoverageRepository, FindingRepository, ScanRepository
from app.policy.field_policy import DEFAULT_POLICY
from app.profiling.profiler import Profiler
from app.services.scan_service import ScanService


def _parents(count: int = 20) -> list[dict[str, str]]:
    return [{"Id": f"acc{i:03d}", "Name": f"Account {i:03d}"} for i in range(count)]


def _contacts(account_count: int) -> list[dict[str, str]]:
    return [
        {"Id": f"con{i:03d}", "AccountId": f"acc{i:03d}", "Name": f"Contact {i:03d}"}
        for i in range(account_count)
    ]


def test_strong_norm_flags_childless_parents():
    records = _parents()
    ctx = DetectionContext(
        object_name="Account",
        records=records,
        fields=["Id", "Name"],
        policy=DEFAULT_POLICY,
        related_records={"Contact": _contacts(17)},
    )

    cands = MissingChildrenDetector().detect(ctx)

    assert len(cands) == 3
    assert {c.record_id for c in cands} == {"acc017", "acc018", "acc019"}
    assert {c.finding_type for c in cands} == {"missing_children"}
    assert all(c.evidence["coverage_rate"] == 0.85 for c in cands)
    assert all(c.evidence["child_object"] == "Contact" for c in cands)


def test_weak_norm_noops():
    ctx = DetectionContext(
        object_name="Account",
        records=_parents(),
        fields=["Id", "Name"],
        policy=DEFAULT_POLICY,
        related_records={"Contact": _contacts(10)},
    )

    assert MissingChildrenDetector().detect(ctx) == []


def test_no_related_data_and_small_parent_population_noop():
    no_related = DetectionContext(
        object_name="Account",
        records=_parents(),
        fields=["Id", "Name"],
        policy=DEFAULT_POLICY,
    )
    assert MissingChildrenDetector().detect(no_related) == []

    too_few = DetectionContext(
        object_name="Account",
        records=_parents(9),
        fields=["Id", "Name"],
        policy=DEFAULT_POLICY,
        related_records={"Contact": _contacts(8)},
    )
    assert MissingChildrenDetector().detect(too_few) == []


def test_scan_service_wiring_with_related_records(tmp_path: Path):
    records = _parents()
    related_records = {"Contact": _contacts(17)}
    schema = ObjectSchema(object_name="Account", fields={})

    class FakeSource(DataSource):
        async def load(self) -> LoadedDataset:
            return LoadedDataset(
                object_name="Account",
                source_name="fake",
                fields=["Id", "Name"],
                records=records,
                source_hash="fakehash",
                schema=schema,
                related_records=related_records,
            )

    db_path = tmp_path / "t.db"
    database = Database(db_path)
    database.setup()
    findings = FindingRepository()
    svc = ScanService(
        database=database,
        profiler=Profiler(),
        grader=CoverageGrader(DEFAULT_POLICY),
        registry=build_default_registry(),
        scans=ScanRepository(),
        coverage_repo=CoverageRepository(),
        findings=findings,
        policy=DEFAULT_POLICY,
    )

    asyncio.run(svc.run_scan(FakeSource()))

    with Database(db_path).connect() as conn:
        frows = findings.list(conn)
    assert "missing_children" in {f["finding_type"] for f in frows}


def test_demo_regression_no_missing_children():
    root = Path(__file__).resolve().parents[1]
    demo = root / "data" / "demo_opportunities.csv"
    with open(demo, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        fields = [field for field in (reader.fieldnames or []) if field]
        records = [{k: clean(v) for k, v in row.items() if k is not None} for row in reader]
    ctx = DetectionContext(
        object_name="Opportunity",
        records=records,
        fields=fields,
        policy=DEFAULT_POLICY,
    )

    cands = build_default_registry().detect(ctx)

    assert len(cands) == 10
    assert sum(1 for c in cands if c.finding_type == "missing_children") == 0
