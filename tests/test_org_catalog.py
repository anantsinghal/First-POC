from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from app.coverage.grader import CoverageGrader
from app.detection.registry import build_default_registry
from app.domain.schema import ObjectSchema, schema_from_dict
from app.ingestion.base import DataSource, LoadedDataset
from app.persistence.database import Database
from app.persistence.repositories import CoverageRepository, FindingRepository, ScanRepository
from app.policy.field_policy import DEFAULT_POLICY
from app.profiling.profiler import Profiler
from app.services.scan_service import ScanService


def test_object_schema_roundtrip_with_to_dict():
    """Round-trip via to_dict + schema_from_dict preserves fields exactly."""
    schema_data = {
        "object_name": "Opportunity",
        "fields": [
            {
                "name": "Id",
                "data_type": "id",
                "required": False,
                "picklist_values": None,
                "reference_to": [],
                "is_name_field": False,
                "is_unique": True,
                "length": 18,
            },
            {
                "name": "StageName",
                "data_type": "picklist",
                "required": True,
                "picklist_values": ["Prospecting", "Closed Won", "Negotiation"],
                "reference_to": [],
                "is_name_field": False,
                "is_unique": False,
                "length": None,
            },
            {
                "name": "AccountId",
                "data_type": "reference",
                "required": True,
                "picklist_values": None,
                "reference_to": ["Account"],
                "is_name_field": False,
                "is_unique": False,
                "length": None,
            },
            {
                "name": "Amount",
                "data_type": "number",
                "required": False,
                "picklist_values": None,
                "reference_to": [],
                "is_name_field": False,
                "is_unique": False,
                "length": None,
            },
        ],
    }
    s = schema_from_dict(schema_data)
    d = s.to_dict()
    roundtrip = schema_from_dict(d)

    assert roundtrip.object_name == s.object_name
    assert set(roundtrip.fields.keys()) == set(s.fields.keys())
    for name, fd in s.fields.items():
        rfd = roundtrip.fields[name]
        assert rfd.name == fd.name
        assert rfd.data_type == fd.data_type
        assert rfd.required == fd.required
        # picklist_values roundtrips as frozenset vs sorted list
        expected_pvals = set(fd.picklist_values) if fd.picklist_values else None
        actual_pvals = set(rfd.picklist_values) if rfd.picklist_values else None
        assert actual_pvals == expected_pvals
        assert list(rfd.reference_to) == list(fd.reference_to)
        assert rfd.is_name_field == fd.is_name_field
        assert rfd.is_unique == fd.is_unique
        assert rfd.length == fd.length


def test_scan_schema_persistence_roundtrip(tmp_path: Path):
    """Schema dict is persisted via save_schema and retrievable via get_schema after reopen."""
    db = Database(tmp_path / "catalog.db")
    db.setup()

    scan_repo = ScanRepository()
    scan_id = "schema-scan-001"
    created = "2026-06-22T00:00:00+00:00"
    source_hash = "abc123"
    schema_dict = {
        "object_name": "Opportunity",
        "fields": [
            {"name": "Id", "data_type": "id", "required": False, "picklist_values": None, "reference_to": []},
            {"name": "Name", "data_type": "string", "required": True, "picklist_values": None, "reference_to": []},
        ],
    }

    with db.connect() as conn:
        scan_repo.insert_running(
            conn,
            scan_id=scan_id,
            source_name="sf:example/Opportunity",
            object_name="Opportunity",
            created_at=created,
            row_count=5,
            source_hash=source_hash,
            fields=["Id", "Name"],
        )
        scan_repo.complete(
            conn,
            scan_id=scan_id,
            completed_at=created,
            profile={"record_count": 5, "fields": {}},
            notes="0 candidate occurrences generated.",
        )
        scan_repo.save_schema(conn, scan_id=scan_id, object_name="Opportunity", schema=schema_dict)

    # Re-open DB and fetch
    db2 = Database(tmp_path / "catalog.db")
    with db2.connect() as conn:
        fetched = scan_repo.get_schema(conn, scan_id)
        assert fetched is not None
        assert fetched["object_name"] == "Opportunity"
        assert len(fetched["fields"]) == 2
        assert fetched["fields"][0]["name"] == "Id"
        assert fetched == schema_dict  # exact match for the dict we saved


class _FakeSchemaSource(DataSource):
    """Fake DataSource that returns a LoadedDataset with schema (for service test)."""

    def __init__(self, schema: ObjectSchema | None, records: list[dict[str, str]] | None = None):
        self._schema = schema
        self._records = records or [{"Id": "006x", "Name": "Test"}]

    async def load(self) -> LoadedDataset:
        return LoadedDataset(
            object_name="Opportunity",
            source_name="fake:Opportunity",
            fields=["Id", "Name"],
            records=self._records,
            source_hash="fakeschemahash",
            schema=self._schema,
        )


def test_scan_service_wires_schema_persistence_and_none_for_csv(tmp_path: Path):
    """ScanService persists schema when present (via to_dict) and returns it in detail; None for CSV-like."""
    db_path = tmp_path / "svc.db"
    database = Database(db_path)
    database.setup()

    profiler = Profiler()
    # Note: grader may need requirements in some versions, but default construction works for coverage
    grader = CoverageGrader(DEFAULT_POLICY)
    registry = build_default_registry()
    scans = ScanRepository()
    coverage_repo = CoverageRepository()
    findings = FindingRepository()

    svc = ScanService(
        database=database,
        profiler=profiler,
        grader=grader,
        registry=registry,
        scans=scans,
        coverage_repo=coverage_repo,
        findings=findings,
        policy=DEFAULT_POLICY,
    )

    # Case 1: with schema
    schema = schema_from_dict({
        "object_name": "Opportunity",
        "fields": [
            {"name": "Id", "data_type": "id", "required": False},
            {"name": "Name", "data_type": "string", "required": True},
        ],
    })
    src_with = _FakeSchemaSource(schema)
    detail_with = asyncio.run(svc.run_scan(src_with))
    assert "schema" in detail_with
    assert detail_with["schema"] is not None
    assert detail_with["schema"]["object_name"] == "Opportunity"
    field_names = [f["name"] for f in detail_with["schema"]["fields"]]
    assert "Id" in field_names and "Name" in field_names
    # also verify via repo directly
    with database.connect() as conn:
        assert scans.get_schema(conn, detail_with["id"]) is not None

    # Case 2: schema=None (CSV style)
    src_none = _FakeSchemaSource(schema=None)
    detail_none = asyncio.run(svc.run_scan(src_none))
    assert "schema" in detail_none
    assert detail_none["schema"] is None
    with database.connect() as conn:
        assert scans.get_schema(conn, detail_none["id"]) is None
