from __future__ import annotations

import csv
from pathlib import Path

from app.detection.base import DetectionContext
from app.detection.schema_driven.format_validity import FormatValidityDetector
from app.detection.registry import build_default_registry
from app.domain.schema import FieldDescriptor, FieldType, ObjectSchema
from app.ingestion.salesforce_schema import schema_from_salesforce_describe
from app.domain.values import clean
from app.policy.field_policy import DEFAULT_POLICY


def test_format_validity_contact_cases():
    """Valid and invalid email/phone/url; empties not flagged."""
    schema = ObjectSchema(
        object_name="Contact",
        fields={
            "Email": FieldDescriptor(name="Email", data_type=FieldType.EMAIL),
            "Phone": FieldDescriptor(name="Phone", data_type=FieldType.PHONE),
            "Website": FieldDescriptor(name="Website", data_type=FieldType.URL),
        },
    )
    records: list[dict[str, str]] = [
        {"Id": "1", "Email": "a@b.com", "Phone": "+1 (415) 555-1234", "Website": "https://x.com"},
        {"Id": "2", "Email": "not-an-email", "Phone": "12", "Website": "x.com"},
        {"Id": "3", "Email": "", "Phone": "", "Website": ""},  # empties, not flagged
    ]
    fields = ["Id", "Email", "Phone", "Website"]
    ctx = DetectionContext(
        object_name="Contact",
        records=records,
        fields=fields,
        policy=DEFAULT_POLICY,
        schema=schema,
    )
    cands = FormatValidityDetector().detect(ctx)
    assert len(cands) == 3
    assert all(c.finding_type == "invalid_format" for c in cands)
    types_by_field = {c.field_name: c for c in cands}
    assert "Email" in types_by_field
    assert "Phone" in types_by_field
    assert "Website" in types_by_field
    # empties not present
    assert len([c for c in cands if c.evidence.get("observed_value") == ""]) == 0


def test_schema_from_salesforce_describe_format_fields():
    """Maps email/phone/url and captures length."""
    describe = {
        "name": "Contact",
        "fields": [
            {"name": "Email", "type": "email", "length": 80},
            {"name": "Phone", "type": "phone", "length": 40},
            {"name": "Website", "type": "url", "length": 255},
        ],
    }
    schema = schema_from_salesforce_describe(describe)
    assert schema.fields["Email"].data_type == FieldType.EMAIL
    assert schema.fields["Email"].length == 80
    assert schema.fields["Phone"].data_type == FieldType.PHONE
    assert schema.fields["Website"].data_type == FieldType.URL


def test_url_accepts_root_relative_paths():
    """Root-relative paths (e.g. Salesforce PhotoUrl-style values) are valid urls, not a defect.

    Found via real-org testing (2026-07-04): Account.PhotoUrl and custom Picture_URL__c fields
    store paths like '/services/images/photo/...' and '/resource/.../pic.png' as normal,
    correct values — these must NOT be flagged as invalid_format.
    """
    schema = ObjectSchema(
        object_name="Account",
        fields={"PhotoUrl": FieldDescriptor(name="PhotoUrl", data_type=FieldType.URL)},
    )
    records = [
        {"Id": "1", "PhotoUrl": "/services/images/photo/001J200000K1PXYIA3"},
        {"Id": "2", "PhotoUrl": "/resource/1701670091000/carhub_images/renault_triber.png"},
        {"Id": "3", "PhotoUrl": "https://example.com/photo.png"},  # absolute still valid
        {"Id": "4", "PhotoUrl": "not a url at all"},  # still correctly invalid (has whitespace, no scheme/leading slash)
    ]
    ctx = DetectionContext(
        object_name="Account", records=records, fields=["Id", "PhotoUrl"],
        policy=DEFAULT_POLICY, schema=schema,
    )
    cands = FormatValidityDetector().detect(ctx)
    flagged_ids = {c.record_id for c in cands}
    assert flagged_ids == {"4"}


def test_format_no_schema_noop():
    records = [{"Id": "1", "Email": "bad"}]
    ctx = DetectionContext(
        object_name="Contact",
        records=records,
        fields=["Id", "Email"],
        policy=DEFAULT_POLICY,
        schema=None,
    )
    assert FormatValidityDetector().detect(ctx) == []


def test_demo_regression_format():
    """Demo via registry unchanged count; zero invalid_format."""
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
    # unchanged count
    assert len(cands) == 10
    assert all(c.finding_type != "invalid_format" for c in cands)
