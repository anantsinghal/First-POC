from __future__ import annotations

import csv
from pathlib import Path

from app.detection.base import DetectionContext
from app.detection.patterns.association import AssociationPatternDetector
from app.detection.registry import build_default_registry
from app.domain.values import clean
from app.policy.field_policy import DEFAULT_POLICY


def _make_synthetic_records(n_breakers: int = 4) -> list[dict[str, str]]:
    """~120 records: Closed Won + Enterprise => Contract__c present at ~95% with exactly n_breakers breakers."""
    recs: list[dict[str, str]] = []
    # 80 non-cohort
    for i in range(80):
        recs.append({
            "Id": f"00{i:016d}AAA",
            "Name": f"Other {i}",
            "StageName": "Prospecting" if i % 2 else "Qualification",
            "Amount": "100000",
            "AccountId": "001000000000000AAA",
            "Contract__c": "a0B000000000000AAA" if i % 3 else "",
            "Segment__c": "SMB",
            "CloseDate": "2026-07-01",
        })
    # cohort ~44 Closed Won + Enterprise
    cohort = 44
    for i in range(cohort):
        has_c = i >= n_breakers  # first n_breakers are breakers
        recs.append({
            "Id": f"00100{i:013d}AAA",
            "Name": f"CW Ent {i}",
            "StageName": "Closed Won",
            "Amount": "95000",
            "AccountId": "001000000000100AAA",
            "Contract__c": "a0B000000000100AAA" if has_c else "",
            "Segment__c": "Enterprise",
            "CloseDate": "2026-08-01",
        })
    return recs


def test_pattern_miner_synthetic():
    """Synthetic data triggers exactly the expected number of pattern_break on Contract__c."""
    recs = _make_synthetic_records(n_breakers=4)
    fields = ["Id", "Name", "StageName", "Amount", "AccountId", "Contract__c", "Segment__c", "CloseDate"]
    ctx = DetectionContext(
        object_name="Opportunity",
        records=recs,
        fields=fields,
        policy=DEFAULT_POLICY,
    )
    cands = AssociationPatternDetector().detect(ctx)
    assert len(cands) == 4
    assert all(c.finding_type == "pattern_break" for c in cands)
    assert all(c.field_name == "Contract__c" for c in cands)
    assert all(c.confidence >= 0.9 for c in cands)
    for c in cands:
        ev = c.evidence
        assert "antecedent" in ev and "support" in ev and "lift" in ev
        assert ev["consequent_field"] == "Contract__c"
        assert ev["observed"] == "absent"


def test_pattern_miner_tiny_dataset():
    """< min_records returns no candidates (guardrail)."""
    recs = [{"Id": f"00{i:016d}AAA", "StageName": "Closed Won", "Segment__c": "Enterprise", "Contract__c": "x"} for i in range(30)]
    ctx = DetectionContext(object_name="Opportunity", records=recs, fields=["Id","StageName","Segment__c","Contract__c"], policy=DEFAULT_POLICY)
    cands = AssociationPatternDetector(min_records=40).detect(ctx)
    assert cands == []


def test_pattern_miner_no_strong_rule():
    """When consequent present rate same as conditioned, no strong rules => []."""
    # all records have Contract__c present at same rate; no lift
    recs = []
    for i in range(50):
        recs.append({"Id": f"00{i:016d}AAA", "StageName": "Closed Won", "Segment__c": "Enterprise", "Contract__c": "x" if i % 2 == 0 else ""})
    ctx = DetectionContext(object_name="Opportunity", records=recs, fields=["Id","StageName","Segment__c","Contract__c"], policy=DEFAULT_POLICY)
    cands = AssociationPatternDetector(min_support_count=5, min_confidence=0.5).detect(ctx)
    assert len(cands) == 0


def test_demo_regression_pattern_miner_absent():
    """11-row demo (<40) yields unchanged count; no pattern_break."""
    root = Path(__file__).resolve().parents[1]
    demo = root / "data" / "demo_opportunities.csv"
    with open(demo, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        fields = [f for f in (reader.fieldnames or []) if f]
        records = [{k: clean(v) for k, v in row.items() if k is not None} for row in reader]
    ctx = DetectionContext(object_name="Opportunity", records=records, fields=fields, policy=DEFAULT_POLICY)
    cands = build_default_registry().detect(ctx)
    # must be same as pre-phase (10)
    assert len(cands) == 10
    types = {c.finding_type for c in cands}
    assert "pattern_break" not in types
