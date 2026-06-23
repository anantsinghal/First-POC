from __future__ import annotations

import csv
import json
from collections import Counter
from pathlib import Path

import pytest

from app.detection.base import DetectionContext
from app.detection.registry import build_default_registry
from app.domain.models import Candidate
from app.domain.schema import schema_from_dict
from app.domain.values import clean
from app.policy.field_policy import DEFAULT_POLICY


def test_full_registry_rich_schema_integration() -> None:
    schema_data = {
        "object_name": "Opportunity",
        "fields": [
            {"name": "Id", "data_type": "id"},
            {
                "name": "Name",
                "data_type": "string",
                "required": True,
                "is_name_field": True,
            },
            {
                "name": "StageName",
                "data_type": "picklist",
                "picklist_values": [
                    "Prospecting",
                    "Qualification",
                    "Closed Won",
                    "Closed Lost",
                ],
            },
            {
                "name": "AccountId",
                "data_type": "reference",
                "required": True,
                "reference_to": ["Account"],
            },
            {"name": "OwnerEmail", "data_type": "email"},
            {"name": "Website", "data_type": "url"},
            {"name": "Amount", "data_type": "number"},
            {
                "name": "Contract__c",
                "data_type": "reference",
                "reference_to": ["Contract"],
            },
            {
                "name": "Segment__c",
                "data_type": "picklist",
                "picklist_values": ["Enterprise", "SMB"],
            },
            {"name": "CloseDate", "data_type": "date"},
        ],
    }
    schema = schema_from_dict(schema_data)
    fields = [field["name"] for field in schema_data["fields"]]
    valid_accts = {f"001ACC{i:03d}" for i in range(60)}
    recs = [
        {
            "Id": f"006OPP{i:03d}",
            "Name": f"Deal {i}",
            "StageName": "Closed Won" if i % 2 == 0 else "Prospecting",
            "AccountId": f"001ACC{i:03d}",
            "OwnerEmail": f"owner{i}@example.com",
            "Website": "https://example.com",
            "Amount": str(100000 + (i % 10) * 1000),
            "Contract__c": f"800CON{i:03d}" if i % 2 == 0 else "",
            "Segment__c": "Enterprise" if i % 3 else "SMB",
            "CloseDate": "2026-12-01",
        }
        for i in range(60)
    ]
    recs[5]["StageName"] = "Frozen"
    recs[6]["Name"] = ""
    recs[7]["OwnerEmail"] = "not-an-email"
    recs[8]["Website"] = "nope"
    recs[9]["AccountId"] = "001MISSING"
    recs[10]["Amount"] = "9000000"
    recs[11]["AccountId"] = ""
    recs[12]["Contract__c"] = ""

    ctx = DetectionContext(
        "Opportunity",
        recs,
        fields,
        DEFAULT_POLICY,
        schema=schema,
        related_ids={"Account": frozenset(valid_accts)},
    )
    reg = build_default_registry()

    for detector in reg.detectors:
        try:
            detector.detect(ctx)
        except Exception as exc:  # pragma: no cover - failure path assertion detail
            pytest.fail(
                f"{detector.__class__.__name__} raised {exc.__class__.__name__}: {exc}"
            )

    findings = reg.detect(ctx)
    finding_types = {finding.finding_type for finding in findings}
    expected_types = {
        "numeric_outlier",
        "robust_outlier",
        "rare_combination",
        "pattern_break",
        "ml_anomaly",
        "required_missing",
        "picklist_invalid",
        "reference_missing",
        "orphan_reference",
        "invalid_format",
        "missing_contract_closed_won",
    }
    assert finding_types >= expected_types, (
        f"missing expected finding types: {expected_types - finding_types}"
    )
    assert len({finding.fingerprint for finding in findings}) == len(findings), (
        "all finding fingerprints must be unique"
    )
    for finding in findings:
        assert_finding_contract(finding)


def test_demo_offline_contract() -> None:
    root = Path(__file__).resolve().parents[1]
    demo = root / "data" / "demo_opportunities.csv"
    with open(demo, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        fields = [field for field in (reader.fieldnames or []) if field]
        records = [
            {key: clean(value) for key, value in row.items() if key is not None}
            for row in reader
        ]

    ctx = DetectionContext(
        object_name="Opportunity",
        records=records,
        fields=fields,
        policy=DEFAULT_POLICY,
    )
    findings = build_default_registry().detect(ctx)
    counts = Counter(finding.finding_type for finding in findings)

    assert len(findings) == 10, f"expected 10 findings, got {len(findings)}"
    assert counts == {
        "numeric_outlier": 1,
        "robust_outlier": 1,
        "rare_combination": 4,
        "missing_contract_closed_won": 1,
        "non_positive_amount": 1,
        "invalid_stage": 1,
        "missing_account": 1,
    }

    absent_types = {
        "required_missing",
        "picklist_invalid",
        "reference_missing",
        "invalid_format",
        "orphan_reference",
        "pattern_break",
        "ml_anomaly",
    }
    present_types = set(counts)
    assert absent_types.isdisjoint(present_types), (
        f"types should be absent without schema or below thresholds: "
        f"{absent_types & present_types}"
    )


def assert_finding_contract(finding: Candidate) -> None:
    assert finding.severity.value in {"high", "medium", "low"}, (
        f"{finding.finding_type} has invalid severity {finding.severity.value!r}"
    )
    assert 0.0 <= finding.confidence <= 1.0, (
        f"{finding.finding_type} confidence out of range: {finding.confidence}"
    )
    assert 0.0 <= finding.priority <= 1.0, (
        f"{finding.finding_type} priority out of range: {finding.priority}"
    )
    assert finding.record_id, f"{finding.finding_type} has empty record_id"
    assert isinstance(finding.evidence, dict), (
        f"{finding.finding_type} evidence must be a dict"
    )
    assert "why" in finding.evidence, (
        f"{finding.finding_type} evidence must include why"
    )
    json.dumps(finding.evidence)
