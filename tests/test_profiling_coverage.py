from __future__ import annotations

import csv
from pathlib import Path

from app.coverage.grader import CoverageGrader
from app.detection.registry import build_default_registry
from app.domain.enums import CoverageGrade
from app.domain.values import clean
from app.policy.field_policy import DEFAULT_POLICY
from app.profiling.profiler import Profiler


def _load_demo_records() -> tuple[list[dict[str, str]], list[str]]:
    """Load 11-row demo CSV using csv.DictReader + clean (matching other phase tests)."""
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


def test_coverage_grader_good_on_full_demo():
    """Full demo fields on Opportunity yields GOOD with no missing/suppressed."""
    records, fields = _load_demo_records()
    cov = CoverageGrader(DEFAULT_POLICY).grade("Opportunity", records, fields)
    assert cov.grade == CoverageGrade.GOOD
    assert cov.missing_fields == []
    assert cov.suppressed_checks == []
    assert cov.observed_records == 11
    assert cov.object_name == "Opportunity"


def test_coverage_grader_partial_missing_expected_fields():
    """Partial fields triggers PARTIAL and the expected suppressed messages for Opportunity."""
    records, _ = _load_demo_records()
    partial_fields = ["Id", "Name", "StageName"]
    # use subset of records that have only these, but any records ok as long as fields
    cov = CoverageGrader(DEFAULT_POLICY, requirements=build_default_registry().requirements()).grade("Opportunity", records, partial_fields)
    assert cov.grade == CoverageGrade.PARTIAL
    assert "Contract__c" in cov.missing_fields or "AccountId" in cov.missing_fields
    assert any("Contract__c" in msg for msg in cov.suppressed_checks)
    assert any("AccountId" in msg for msg in cov.suppressed_checks)


def test_coverage_grader_blocked_empty_records():
    """Empty records always BLOCKED."""
    cov = CoverageGrader(DEFAULT_POLICY).grade("Opportunity", [], ["Id", "Name"])
    assert cov.grade == CoverageGrade.BLOCKED
    assert cov.observed_records == 0


def test_profiler_profile_matches_baseline_numeric_and_count():
    """Profiler on demo produces Profile whose to_dict matches expected shape and Amount stats."""
    records, fields = _load_demo_records()
    prof = Profiler().profile(records, fields)
    d = prof.to_dict()
    assert d["record_count"] == 11
    amount = d["fields"]["Amount"]
    assert amount["numeric"]["min"] == -5000.0
    assert amount["numeric"]["max"] == 2500000.0
    # also basic shape
    assert "null_rate" in amount
    assert "distinct_count" in amount
    assert "top_values" in amount
    assert "numeric" in amount


def test_coverage_reports_closedate_dependent_checks():
    """When CloseDate absent on Opportunity, grader with requirements reports CloseDate-dependent checks (e.g. Stale)."""
    records, _ = _load_demo_records()
    fields_without_close = ["Id", "Name", "StageName", "Amount", "AccountId", "Contract__c", "Segment__c"]
    cov = CoverageGrader(
        DEFAULT_POLICY,
        requirements=build_default_registry().requirements(),
    ).grade("Opportunity", records, fields_without_close)
    assert any("CloseDate" in m for m in cov.suppressed_checks)
    assert any('past its Close Date' in m for m in cov.suppressed_checks)
