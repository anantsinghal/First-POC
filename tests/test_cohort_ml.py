from __future__ import annotations

import pytest

pytest.importorskip("sklearn")

from app.detection.ml.isolation_forest import IsolationForestDetector
from tests import harness


def _segmented_dataset() -> tuple[list[dict[str, str]], object]:
    """120 records in two segments: Enterprise (~1M) and SMB (~10k), with one SMB record
    that has an Enterprise-sized Amount (anomalous WITHIN the SMB cohort, normal globally)."""
    recs: list[dict[str, str]] = []
    for i in range(60):
        recs.append({"Id": f"ENT{i:03d}", "Name": f"E{i}", "Segment__c": "Enterprise",
                     "Amount": str(1_000_000 + (i % 10) * 500)})
    for i in range(60):
        recs.append({"Id": f"SMB{i:03d}", "Name": f"S{i}", "Segment__c": "SMB",
                     "Amount": str(10_000 + (i % 10) * 50)})
    recs[60 + 5]["Amount"] = "1000000"  # SMB005: huge for SMB, ordinary for Enterprise
    schema = harness.make_schema("Deal__c", [
        {"name": "Id", "data_type": "id"},
        {"name": "Name", "data_type": "string"},
        {"name": "Segment__c", "data_type": "picklist", "picklist_values": ["Enterprise", "SMB"]},
        {"name": "Amount", "data_type": "number"},
    ])
    return recs, schema


def test_cohort_relative_ml_flags_within_cohort_outlier():
    recs, schema = _segmented_dataset()
    ctx = harness.context("Deal__c", recs, schema=schema)
    findings = IsolationForestDetector(contamination=0.05).detect(ctx)
    flagged = {f.record_id for f in findings}
    assert "SMB005" in flagged, "within-cohort Amount outlier should be flagged"
    smb = next(f for f in findings if f.record_id == "SMB005")
    # Proven cohort-relative: scored against the 60-record SMB cohort, not all 120.
    assert smb.evidence.get("cohort") == "SMB"
    assert smb.evidence.get("cohort_field") == "Segment__c"
    assert smb.evidence.get("n_records") == 60
    harness.assert_healthy(findings)


def test_cohort_ml_is_deterministic():
    recs, schema = _segmented_dataset()
    ctx = harness.context("Deal__c", recs, schema=schema)
    det = IsolationForestDetector(contamination=0.05)
    run1 = sorted((f.record_id, f.evidence["anomaly_score"]) for f in det.detect(ctx))
    run2 = sorted((f.record_id, f.evidence["anomaly_score"]) for f in det.detect(ctx))
    assert run1 == run2


def test_no_schema_uses_whole_object_population():
    """Without a schema there is no cohort field → single whole-object model (n_records == all)."""
    recs, _ = _segmented_dataset()
    ctx = harness.context("Deal__c", recs)
    findings = IsolationForestDetector(contamination=0.05).detect(ctx)
    for f in findings:
        assert "cohort" not in f.evidence
        assert f.evidence["n_records"] == 120
