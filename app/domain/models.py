from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any

from .enums import CoverageGrade, Severity


Record = dict[str, str]
"""Type alias for a raw record as string-valued dict."""


def stable_hash(value: Any) -> str:
    """Return sha256 hex digest of json.dumps(value, sort_keys=True, default=str, separators=(',', ':'))."""
    encoded = json.dumps(value, sort_keys=True, default=str, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


@dataclass(frozen=True)
class Candidate:
    """Immutable candidate finding produced by a detector."""

    object_name: str
    record_id: str
    record_name: str
    finding_type: str
    field_name: str | None
    severity: Severity
    confidence: float
    priority: float
    title: str
    evidence: dict[str, Any]

    @property
    def fingerprint(self) -> str:
        """Stable fingerprint for dedup/upsert using object, record, rule, field."""
        return stable_hash({"object": self.object_name, "record": self.record_id, "rule": self.finding_type, "field": self.field_name})


@dataclass(frozen=True)
class FieldProfile:
    """Profile statistics for a single field."""

    null_rate: float
    distinct_count: int
    top_values: list[tuple[str, int]]
    numeric: dict[str, float] | None = None


@dataclass(frozen=True)
class Profile:
    """Aggregate profile over all fields for a scan."""

    record_count: int
    fields: dict[str, FieldProfile]

    def to_dict(self) -> dict[str, Any]:
        """Return dict matching baseline JSON shape, omitting numeric key when None."""
        fields_dict: dict[str, Any] = {}
        for name, fp in self.fields.items():
            d: dict[str, Any] = {
                "null_rate": fp.null_rate,
                "distinct_count": fp.distinct_count,
                "top_values": fp.top_values,
            }
            if fp.numeric is not None:
                d["numeric"] = fp.numeric
            fields_dict[name] = d
        return {"record_count": self.record_count, "fields": fields_dict}


@dataclass(frozen=True)
class Coverage:
    """Coverage assessment and grade for a scan."""

    object_name: str
    observed_records: int
    queried_fields: list[str]
    missing_fields: list[str]
    suppressed_checks: list[str]
    grade: CoverageGrade
    reason: str

    def to_dict(self) -> dict[str, Any]:
        """Return dict with grade as string value, keys exactly as baseline."""
        return {
            "object_name": self.object_name,
            "observed_records": self.observed_records,
            "queried_fields": self.queried_fields,
            "missing_fields": self.missing_fields,
            "suppressed_checks": self.suppressed_checks,
            "grade": self.grade.value,
            "reason": self.reason,
        }
