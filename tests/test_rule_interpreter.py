from __future__ import annotations

from datetime import date

from app.detection.base import DetectionContext
from app.detection.rules.interpreter import RuleInterpreterDetector
from app.domain.values import clean
from app.policy.field_policy import DEFAULT_POLICY
from app.policy.rules import load_rule_catalog


def test_load_rule_catalog():
    rules = load_rule_catalog()
    assert len(rules) >= 4
    finding_types = {r.finding_type for r in rules}
    assert "missing_contract_closed_won" in finding_types
    assert "non_positive_amount" in finding_types
    assert "stale_open_opportunity" in finding_types
    assert "missing_close_date" in finding_types


def test_rule_interpreter_fires_correctly():
    # Records exercising the four rules
    records: list[dict[str, str]] = [
        # missing_contract_closed_won
        {"Id": "1", "StageName": "Closed Won", "Contract__c": ""},
        # non_positive_amount
        {"Id": "2", "Amount": "-100"},
        # stale_open_opportunity (with injected today)
        {"Id": "3", "StageName": "Prospecting", "CloseDate": "2020-01-01"},
        # missing_close_date
        {"Id": "4", "CloseDate": ""},
        # good record
        {"Id": "5", "StageName": "Closed Won", "Contract__c": "c1", "Amount": "100", "CloseDate": "2026-12-31"},
    ]
    fields = ["Id", "StageName", "Contract__c", "Amount", "CloseDate"]
    ctx = DetectionContext(
        object_name="Opportunity",
        records=records,
        fields=fields,
        policy=DEFAULT_POLICY,
    )
    det = RuleInterpreterDetector(load_rule_catalog(), today=date(2026, 1, 1))
    cands = det.detect(ctx)
    types = {c.finding_type for c in cands}
    assert "missing_contract_closed_won" in types
    assert "non_positive_amount" in types
    assert "stale_open_opportunity" in types
    assert "missing_close_date" in types
    assert len([c for c in cands if c.finding_type == "missing_contract_closed_won"]) == 1
    # stale should have fired for record 3
    stale = [c for c in cands if c.finding_type == "stale_open_opportunity"]
    assert len(stale) == 1


def test_rule_skipped_when_field_absent():
    records = [{"Id": "1", "StageName": "Closed Won", "Contract__c": ""}]
    # missing Contract__c in fields
    ctx = DetectionContext(
        object_name="Opportunity",
        records=records,
        fields=["Id", "StageName"],  # Contract__c absent
        policy=DEFAULT_POLICY,
    )
    det = RuleInterpreterDetector(load_rule_catalog())
    cands = det.detect(ctx)
    # the contract rule should be skipped
    assert "missing_contract_closed_won" not in {c.finding_type for c in cands}


def test_coverage_requirements_contract():
    det = RuleInterpreterDetector(load_rule_catalog())
    reqs = det.coverage_requirements()
    labels = [r.label for r in reqs]
    assert any("contract" in (l or "").lower() for l in labels)
    # at least one should require Contract__c
    requires_contract = any("Contract__c" in (r.requires or ()) for r in reqs)
    assert requires_contract


def test_new_rules_fire_and_do_not_fire():
    """The three newly catalogued rules (invalid_stage, missing_account, missing_required) fire on violations and do not on valid data."""
    # Bad data: out-of-list stage, empty AccountId, empty Name
    bad_records = [
        {"Id": "bad1", "StageName": "NotAStage", "AccountId": "", "Name": ""},
    ]
    fields = ["Id", "StageName", "AccountId", "Name"]
    ctx_bad = DetectionContext(
        object_name="Opportunity",
        records=bad_records,
        fields=fields,
        policy=DEFAULT_POLICY,
    )
    det = RuleInterpreterDetector(load_rule_catalog())
    cands_bad = det.detect(ctx_bad)
    types_bad = {c.finding_type for c in cands_bad}
    assert "invalid_stage" in types_bad
    assert "missing_account" in types_bad
    assert "missing_required" in types_bad

    # Good data: valid stage, present AccountId, present Name
    good_records = [
        {"Id": "good1", "StageName": "Closed Won", "AccountId": "acc1", "Name": "GoodName"},
    ]
    ctx_good = DetectionContext(
        object_name="Opportunity",
        records=good_records,
        fields=fields,
        policy=DEFAULT_POLICY,
    )
    cands_good = det.detect(ctx_good)
    types_good = {c.finding_type for c in cands_good}
    assert "invalid_stage" not in types_good
    assert "missing_account" not in types_good
    assert "missing_required" not in types_good
