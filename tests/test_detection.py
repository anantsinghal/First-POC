from __future__ import annotations

import csv
from pathlib import Path

import pytest

from app.detection.base import DetectionContext
from app.detection.registry import build_default_registry
from app.domain.values import clean
from app.policy.field_policy import DEFAULT_POLICY


def _load_demo_records() -> tuple[list[dict[str, str]], list[str]]:
    """Load the 11-row demo CSV, clean values like the baseline."""
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


def test_default_registry_detects_expected_demo_findings():
    """Default detectors on full Opportunity demo must emit the 4 non-required types mentioned."""
    records, fields = _load_demo_records()
    ctx = DetectionContext(
        object_name="Opportunity",
        records=records,
        fields=fields,
        policy=DEFAULT_POLICY,
    )
    registry = build_default_registry()
    cands = registry.detect(ctx)
    types = {c.finding_type for c in cands}
    assert "invalid_stage" in types
    assert "missing_account" in types
    assert "missing_contract_closed_won" in types
    assert "non_positive_amount" in types


def test_fingerprint_stability():
    """Identical record/rule/field produces identical fingerprint (for dedup)."""
    records, fields = _load_demo_records()
    ctx = DetectionContext(
        object_name="Opportunity",
        records=records,
        fields=fields,
        policy=DEFAULT_POLICY,
    )
    registry = build_default_registry()
    cands1 = registry.detect(ctx)
    cands2 = registry.detect(ctx)
    # pick one of each non-trivial type and compare fingerprints
    by_type1 = {c.finding_type: c for c in cands1}
    by_type2 = {c.finding_type: c for c in cands2}
    for t in ("invalid_stage", "missing_account", "missing_contract_closed_won", "non_positive_amount"):
        if t in by_type1 and t in by_type2:
            assert by_type1[t].fingerprint == by_type2[t].fingerprint
            # also verify structure
            assert by_type1[t].object_name == "Opportunity"
            assert by_type1[t].record_id  # non-empty
