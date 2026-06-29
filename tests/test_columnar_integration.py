from __future__ import annotations

import glob
import tempfile
from pathlib import Path

import pytest

from app.planning.planner import ScanPlanner
from tests import harness


def test_planner_columnar_threshold():
    planner = ScanPlanner(columnar_profile_min_records=100_000)
    assert planner.use_columnar_profile(100_000) is True
    assert planner.use_columnar_profile(250_000) is True
    assert planner.use_columnar_profile(10) is False


def _build_service(planner):
    """Build a ScanService from a tmp container, swapping in a custom planner."""
    from app.api.container import build_container

    container = build_container(data_dir=Path(tempfile.mkdtemp()), root=harness.ROOT)
    container.database.setup()
    svc = container.scan_service
    svc.planner = planner  # ScanService isn't frozen; override the planner for this test
    return container, svc


def test_columnar_profile_selected_for_large_objects_matches_in_memory():
    pytest.importorskip("duckdb")
    pytest.importorskip("pyarrow")
    import asyncio

    recs, fields = harness.demo_records()
    dataset = harness.FakeSource  # noqa: F841 (clarity)

    # Force the columnar path by setting the threshold below the demo size.
    _, svc_col = _build_service(ScanPlanner(columnar_profile_min_records=1))
    scan_col = asyncio.run(svc_col.run_scan(harness.FakeSource(_demo_dataset(recs, fields))))

    # In-memory path (high threshold) for comparison.
    _, svc_mem = _build_service(ScanPlanner(columnar_profile_min_records=10_000_000))
    scan_mem = asyncio.run(svc_mem.run_scan(harness.FakeSource(_demo_dataset(recs, fields))))

    # Same profile + same finding count regardless of which profiler ran.
    assert scan_col["profile"] == scan_mem["profile"]
    assert scan_col["finding_count"] == scan_mem["finding_count"] == 10
    assert scan_col["profile"]["record_count"] == 11


def test_columnar_staging_is_ephemeral():
    pytest.importorskip("duckdb")
    pytest.importorskip("pyarrow")
    import asyncio

    before = set(glob.glob(f"{tempfile.gettempdir()}/scan_stage_*"))
    recs, fields = harness.demo_records()
    _, svc = _build_service(ScanPlanner(columnar_profile_min_records=1))
    asyncio.run(svc.run_scan(harness.FakeSource(_demo_dataset(recs, fields))))
    after = set(glob.glob(f"{tempfile.gettempdir()}/scan_stage_*"))
    assert after <= before, "staging dirs should be purged after the scan"


def _demo_dataset(records, fields):
    from app.ingestion.base import LoadedDataset

    return LoadedDataset(
        object_name="Opportunity",
        source_name="demo",
        fields=fields,
        records=records,
        source_hash="demohash",
    )
