from __future__ import annotations

import csv
from datetime import date
from pathlib import Path

from app.detection.base import DetectionContext
from app.detection.registry import build_default_registry
from app.detection.rules.completeness import MissingCloseDateDetector
from app.detection.rules.duplicates import DuplicateNameDetector
from app.detection.rules.temporal import StaleOpenOpportunityDetector
from app.domain.values import clean
from app.policy.field_policy import DEFAULT_POLICY


def _load_demo_records() -> tuple[list[dict[str, str]], list[str]]:
    """Minimal loader for demo (duplicated to keep this test self-contained)."""
    root = Path(__file__).resolve().parents[1]
    demo = root / "data" / "demo_opportunities.csv"
    with open(demo, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        fields = [f for f in (reader.fieldnames or []) if f]
        records = [
            {k: clean(v) for k, v in row.items() if k is not None}
            for row in reader
        ]
    return records, fields


def test_stale_open_opportunity_detector():
    """StaleOpen fires only on open stage with past CloseDate; today injected."""
    records: list[dict[str, str]] = [
        {"Id": "1", "Name": "OpenPast", "StageName": "Prospecting", "CloseDate": "2020-01-01"},
        {"Id": "2", "Name": "ClosedPast", "StageName": "Closed Won", "CloseDate": "2020-01-01"},
    ]
    ctx = DetectionContext(
        object_name="Opportunity",
        records=records,
        fields=["Id", "Name", "StageName", "CloseDate"],
        policy=DEFAULT_POLICY,
    )
    det = StaleOpenOpportunityDetector(today=date(2026, 1, 1))
    cands = det.detect(ctx)
    assert len(cands) == 1
    assert cands[0].finding_type == "stale_open_opportunity"
    assert cands[0].field_name == "CloseDate"
    assert cands[0].evidence["as_of"] == "2026-01-01"
    assert cands[0].evidence["stage"] == "Prospecting"


def test_duplicate_name_detector():
    """DuplicateName fires on case-insensitive Name dups, reports count."""
    records: list[dict[str, str]] = [
        {"Id": "1", "Name": "Acme"},
        {"Id": "2", "Name": "acme"},
        {"Id": "3", "Name": "Beta"},
    ]
    ctx = DetectionContext(
        object_name="X",
        records=records,
        fields=["Id", "Name"],
        policy=DEFAULT_POLICY,
    )
    cands = DuplicateNameDetector().detect(ctx)
    assert len(cands) == 2
    assert all(c.finding_type == "duplicate_name" for c in cands)
    assert all(c.field_name == "Name" for c in cands)
    # both should report count 2
    assert cands[0].evidence["duplicate_count"] == 2
    assert cands[1].evidence["duplicate_count"] == 2


def test_missing_close_date_detector():
    """MissingCloseDate fires only on Opportunity with empty CloseDate."""
    records: list[dict[str, str]] = [
        {"Id": "1", "Name": "NoDate", "CloseDate": ""},
        {"Id": "2", "Name": "HasDate", "CloseDate": "2026-12-31"},
    ]
    ctx = DetectionContext(
        object_name="Opportunity",
        records=records,
        fields=["Id", "Name", "CloseDate"],
        policy=DEFAULT_POLICY,
    )
    cands = MissingCloseDateDetector().detect(ctx)
    assert len(cands) == 1
    assert cands[0].finding_type == "missing_close_date"
    assert cands[0].field_name == "CloseDate"
    assert cands[0].evidence["observed_value"] is None


def test_demo_regression_new_detectors_absent():
    """Regression: demo with 11 rows + default registry now yields exactly 10 candidates (robust_outlier on the 2.5M Amount); the three temporal/duplicate/completeness still absent."""
    records, fields = _load_demo_records()
    ctx = DetectionContext(
        object_name="Opportunity",
        records=records,
        fields=fields,
        policy=DEFAULT_POLICY,
    )
    cands = build_default_registry().detect(ctx)
    assert len(cands) == 10, f"Expected 10 candidates from demo, got {len(cands)}"
    types = {c.finding_type for c in cands}
    assert "robust_outlier" in types
    assert sum(1 for c in cands if c.finding_type == "robust_outlier") == 1
    assert "stale_open_opportunity" not in types
    assert "duplicate_name" not in types
    assert "missing_close_date" not in types
    # also verify baseline ones still there (smoke)
    assert "invalid_stage" in types or "missing_account" in types  # at least some
