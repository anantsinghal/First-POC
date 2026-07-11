from __future__ import annotations

import asyncio

from app.coverage.grader import CoverageGrader
from app.domain.enums import CoverageGrade
from app.policy.field_policy import DEFAULT_POLICY
from tests import harness


def test_sampled_scan_is_never_reported_complete():
    """A sampled (record-capped) scan must be PARTIAL, even when all fields are present."""
    records = [{"Id": f"00Q{i:03d}", "Name": f"R{i}"} for i in range(50)]
    grader = CoverageGrader(DEFAULT_POLICY)
    full = grader.grade("Account", records, ["Id", "Name"], sampled=False)
    sampled = grader.grade("Account", records, ["Id", "Name"], sampled=True)
    assert full.grade == CoverageGrade.GOOD
    assert sampled.grade == CoverageGrade.PARTIAL
    assert "Sampled scan" in sampled.reason
    assert any("record cap reached" in s for s in sampled.suppressed_checks)


def test_blocked_stays_blocked_even_if_sampled():
    grader = CoverageGrader(DEFAULT_POLICY)
    cov = grader.grade("Account", [], ["Id"], sampled=True)
    assert cov.grade == CoverageGrade.BLOCKED


def test_salesforce_source_exact_max_records_not_truncated(tmp_path):
    """Exactly max_records rows (no +1) is NOT truncated; coverage stays good (Phase 68 F10 fix).

    Updated from the prior off-by-one assertion (len>=max was falsely 'sampled').
    """
    describe = {
        "name": "Account",
        "fields": [
            {"name": "Id", "type": "id", "queryable": True, "nillable": False},
            {"name": "Name", "type": "string", "queryable": True, "nillable": False},
        ],
    }
    query_records = [{"Id": f"001{i:03d}", "Name": f"A{i}"} for i in range(5)]
    src = harness.salesforce_source("Account", describe=describe, query_records=query_records, max_records=5)
    ds = asyncio.run(src.load())
    assert ds.truncated is False
    assert len(ds.records) == 5
    scan, _ = harness.run_scan(ds, tmp_path)
    assert scan["coverage"]["grade"] == "good"


def test_salesforce_source_max_plus_one_is_truncated(tmp_path):
    """max_records+1 available rows -> truncated True, coverage partial (record cap)."""
    describe = {
        "name": "Account",
        "fields": [
            {"name": "Id", "type": "id", "queryable": True, "nillable": False},
            {"name": "Name", "type": "string", "queryable": True, "nillable": False},
        ],
    }
    query_records = [{"Id": f"001{i:03d}", "Name": f"A{i}"} for i in range(6)]
    src = harness.salesforce_source("Account", describe=describe, query_records=query_records, max_records=5)
    ds = asyncio.run(src.load())
    assert ds.truncated is True
    assert len(ds.records) == 5
    scan, _ = harness.run_scan(ds, tmp_path)
    assert scan["coverage"]["grade"] == "partial"
    assert any("record cap" in s for s in scan["coverage"]["suppressed_checks"])
