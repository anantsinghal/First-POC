from __future__ import annotations

import asyncio
from pathlib import Path

from app.api.container import build_container
from app.detection.registry import build_default_registry
from app.ingestion.base import LoadedDataset
from app.planning.planner import DETECTOR_TIERS, ScanPlanner
from app.services.scan_service import ScanService
from tests import harness


def test_planner_unit_tiers() -> None:
    """Planner includes or defers detectors exactly by configured thresholds."""
    planner = ScanPlanner()

    tiny = planner.plan("Account", 10)
    assert tiny.skipped == ()
    assert "ml_anomaly" in tiny.included_ids
    assert "pattern_break" in tiny.included_ids
    assert set(DETECTOR_TIERS).issubset(tiny.included_ids)

    huge = planner.plan("Account", 5_000_000)
    assert "required_missing" in huge.included_ids
    assert "ml_anomaly" not in huge.included_ids
    assert "pattern_break" not in huge.included_ids
    assert "numeric_outlier" not in huge.included_ids
    assert huge.skipped
    skipped_reasons = dict(huge.skipped)
    assert skipped_reasons["ml_anomaly"] == (
        "ML scoring deferred: 5000000 exceeds 200000 (needs sampling)"
    )
    assert skipped_reasons["pattern_break"] == (
        "pattern mining deferred: 5000000 exceeds 500000 (needs sampling)"
    )
    assert skipped_reasons["numeric_outlier"] == (
        "tier-1 detector deferred: 5000000 records exceeds 2000000 (needs columnar/streaming)"
    )

    medium = planner.plan("Account", 300_000)
    assert "ml_anomaly" not in medium.included_ids
    assert "pattern_break" in medium.included_ids
    assert "duplicate_name" in medium.included_ids
    assert dict(medium.skipped)["ml_anomaly"] == (
        "ML scoring deferred: 300000 exceeds 200000 (needs sampling)"
    )


def test_registry_filter_via_harness() -> None:
    """Registry filtering excludes requested detectors while default detect remains unchanged."""
    ctx = harness.rich_opportunity_context()
    registry = build_default_registry()

    unfiltered = harness.detect_all(ctx)
    explicit_all = registry.detect(ctx)
    filtered = registry.detect(
        ctx,
        include=frozenset(detector_id for detector_id in DETECTOR_TIERS if detector_id not in {"ml_anomaly", "pattern_break"}),
    )

    assert len(unfiltered) == len(explicit_all)
    assert all(f.finding_type not in {"ml_anomaly", "pattern_break"} for f in filtered)
    assert len(filtered) <= len(unfiltered)


def test_service_path_records_deferred_detectors(tmp_path: Path) -> None:
    """Tiny thresholds defer ML and pattern detectors and record that in audit output."""
    records, fields = harness.demo_records()
    dataset = LoadedDataset(
        object_name="Opportunity",
        source_name="demo_opportunities.csv",
        fields=fields,
        records=records,
        source_hash="demo-scan-planner-test",
    )
    container = build_container(data_dir=tmp_path, root=harness.ROOT)
    service = ScanService(
        database=container.scan_service.database,
        profiler=container.scan_service.profiler,
        grader=container.scan_service.grader,
        registry=container.scan_service.registry,
        planner=ScanPlanner(ml_max_records=5, pattern_max_records=5),
        scans=container.scan_service.scans,
        coverage_repo=container.scan_service.coverage_repo,
        findings=container.scan_service.findings,
        policy=container.scan_service.policy,
        audit=container.scan_service.audit,
    )

    scan = asyncio.run(service.run_scan(harness.FakeSource(dataset)))
    findings = container.finding_service.list_findings(scan_id=scan["id"])
    audits = container.audit_service.list(limit=20)
    completed = next(entry for entry in audits if entry["event"] == "scan_completed")

    assert scan["status"] == "completed"
    assert scan["finding_count"] == 10
    assert len(findings) == 10
    assert "all detectors planned" not in scan["notes"]
    assert completed["detail"]["deferred_detectors"] == ["pattern_break", "ml_anomaly"]
    assert completed["detail"]["planned_detectors"] == len(DETECTOR_TIERS) - 2


def test_demo_regression_default_planner(tmp_path: Path) -> None:
    """Default planner keeps the normal demo scan unchanged."""
    records, fields = harness.demo_records()
    dataset = LoadedDataset(
        object_name="Opportunity",
        source_name="demo_opportunities.csv",
        fields=fields,
        records=records,
        source_hash="demo-default-planner-test",
    )

    scan, findings = harness.run_scan(dataset, tmp_path)

    assert scan["status"] == "completed"
    assert scan["finding_count"] == 10
    assert len(findings) == 10
    assert scan["notes"] == "10 candidate occurrences generated. all detectors planned"
