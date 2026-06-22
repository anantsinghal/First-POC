from __future__ import annotations

import csv
from pathlib import Path

import pytest

pytest.importorskip("sklearn")

from app.detection.base import DetectionContext
from app.detection.ml.isolation_forest import IsolationForestDetector
from app.detection.registry import build_default_registry
from app.domain.values import clean
from app.policy.field_policy import DEFAULT_POLICY


def _make_synthetic_records() -> list[dict[str, str]]:
    """~120 records: 115 normal + 5 clear outliers."""
    recs: list[dict[str, str]] = []
    # normal
    for i in range(115):
        recs.append({
            "Id": f"00{i:016d}AAA",
            "Name": f"Normal {i}",
            "StageName": "Prospecting" if i % 3 else "Qualification",
            "Amount": str(100000 + (i % 10) * 1000 - 5000),
            "AccountId": "001000000000000AAA",
            "Contract__c": "a0B000000000000AAA",
            "Segment__c": "SMB",
            "CloseDate": "2026-07-01",
        })
    # 5 outliers
    outliers = [
        {"Amount": "5000000", "StageName": "Closed Won", "Segment__c": "Enterprise"},
        {"Amount": "4999999", "StageName": "Proposal", "Segment__c": "SMB"},
        {"Amount": "100000", "StageName": "Closed Won", "Segment__c": "Enterprise"},
        {"Amount": "5000001", "StageName": "Negotiation/Review", "Segment__c": "Mid-Market"},
        {"Amount": "100", "StageName": "Closed Won", "Segment__c": "Enterprise"},
    ]
    for j, o in enumerate(outliers):
        recs.append({
            "Id": f"00{1000000000000000 + j:015d}AAA",
            "Name": f"Outlier {j}",
            "StageName": o.get("StageName", "Closed Won"),
            "Amount": o["Amount"],
            "AccountId": "001000000000999AAA",
            "Contract__c": "a0B000000000999AAA",
            "Segment__c": o.get("Segment__c", "Enterprise"),
            "CloseDate": "2026-12-31",
        })
    return recs


def test_ml_anomaly_synthetic_outliers():
    recs = _make_synthetic_records()
    fields = list(recs[0].keys())
    ctx = DetectionContext(
        object_name="Opportunity",
        records=recs,
        fields=fields,
        policy=DEFAULT_POLICY,
    )
    cands = IsolationForestDetector(contamination=0.05).detect(ctx)
    assert len(cands) >= 1
    assert all(c.finding_type == "ml_anomaly" for c in cands)
    outlier_ids = {r["Id"] for r in recs[-5:]}
    flagged_ids = {c.record_id for c in cands}
    assert len(outlier_ids & flagged_ids) >= 1  # at least some of the injected are caught
    for c in cands:
        ev = c.evidence
        assert "anomaly_score" in ev
        assert "top_contributors" in ev and len(ev["top_contributors"]) > 0
        assert 0.55 <= c.confidence <= 0.96


def test_ml_anomaly_reproducibility():
    recs = _make_synthetic_records()
    fields = list(recs[0].keys())
    ctx = DetectionContext(
        object_name="Opportunity",
        records=recs,
        fields=fields,
        policy=DEFAULT_POLICY,
    )
    d1 = IsolationForestDetector(random_state=42, contamination=0.05).detect(ctx)
    d2 = IsolationForestDetector(random_state=42, contamination=0.05).detect(ctx)
    ids1 = sorted(c.record_id for c in d1)
    ids2 = sorted(c.record_id for c in d2)
    assert ids1 == ids2
    scores1 = sorted(round(float(c.evidence["anomaly_score"]), 4) for c in d1)
    scores2 = sorted(round(float(c.evidence["anomaly_score"]), 4) for c in d2)
    assert scores1 == scores2


def test_ml_anomaly_tiny_dataset():
    recs = [{"Id": f"00{i:016d}AAA", "Amount": "100000"} for i in range(40)]
    ctx = DetectionContext(
        object_name="Opportunity",
        records=recs,
        fields=["Id", "Amount"],
        policy=DEFAULT_POLICY,
    )
    cands = IsolationForestDetector(min_records=50).detect(ctx)
    assert cands == []


def test_demo_regression_ml_anomaly_absent():
    """11-row demo (<50) yields unchanged count; no ml_anomaly."""
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
    # unchanged from previous (10)
    assert len(cands) == 10
    types = {c.finding_type for c in cands}
    assert "ml_anomaly" not in types
