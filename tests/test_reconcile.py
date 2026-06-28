from __future__ import annotations

import pytest

pytest.importorskip("sklearn")

from app.detection.reconcile import reconcile
from tests import harness


def _by_record(findings) -> dict[str, set[str]]:
    out: dict[str, set[str]] = {}
    for f in findings:
        out.setdefault(f.record_id, set()).add(f.finding_type)
    return out


def test_schema_supersedes_rule_duplicates():
    """With a schema present, metadata-derived findings supersede rule-catalog duplicates."""
    findings = harness.detect_all(harness.rich_opportunity_context())
    by_rec = _by_record(findings)
    # 006OPP005 has StageName 'Frozen'
    assert "picklist_invalid" in by_rec.get("006OPP005", set())
    assert "invalid_stage" not in by_rec.get("006OPP005", set())
    # 006OPP011 has empty (required) AccountId
    assert "reference_missing" in by_rec.get("006OPP011", set())
    assert "missing_account" not in by_rec.get("006OPP011", set())
    # 006OPP006 has empty (required) Name
    assert "required_missing" in by_rec.get("006OPP006", set())
    assert "missing_required" not in by_rec.get("006OPP006", set())
    harness.assert_healthy(findings)


def test_no_schema_keeps_rule_findings():
    """Without a schema there are no metadata duplicates, so rule findings are kept (demo unchanged)."""
    recs, fields = harness.demo_records()
    findings = harness.detect_all(harness.context("Opportunity", recs, fields=fields))
    types = harness.type_counts(findings)
    assert len(findings) == 10
    assert "invalid_stage" in types
    assert "missing_account" in types
    assert "picklist_invalid" not in types


def test_corroborating_outliers_preserved():
    """numeric_outlier + robust_outlier are NOT an equivalence group — both are kept."""
    recs, fields = harness.demo_records()
    types = harness.type_counts(harness.detect_all(harness.context("Opportunity", recs, fields=fields)))
    assert types.get("numeric_outlier", 0) >= 1
    assert types.get("robust_outlier", 0) >= 1


def test_reconcile_unit_keeps_only_top_precedence():
    """Direct unit: two findings of the same group on one record/field -> highest precedence kept."""
    ctx = harness.context(
        "Opportunity",
        [{"Id": "006X", "StageName": "Frozen"}],
        fields=["Id", "StageName"],
    )
    from app.detection.base import make_candidate

    schema_cand = make_candidate(ctx, ctx.records[0], rule="picklist_invalid",
                                 title="Invalid StageName", field_name="StageName", evidence={"why": "schema"})
    rule_cand = make_candidate(ctx, ctx.records[0], rule="invalid_stage",
                               title="Unknown stage", field_name="StageName", evidence={"why": "rule"})
    out = reconcile([rule_cand, schema_cand])
    assert [c.finding_type for c in out] == ["picklist_invalid"]
