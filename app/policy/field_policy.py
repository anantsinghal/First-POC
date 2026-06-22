from __future__ import annotations

from dataclasses import dataclass

from ..domain.enums import Severity


@dataclass(frozen=True)
class FieldPolicy:
    """Policy defining expected fields and rule severity/score mapping."""

    def expected_fields(self, object_name: str) -> set[str]:
        """Return minimal expected fields for object; Id always, plus Opportunity core fields."""
        expected: set[str] = {"Id"}
        if object_name.lower() == "opportunity":
            expected |= {"Name", "StageName", "Amount", "AccountId", "Contract__c"}
        return expected

    def severity_for(self, rule: str) -> tuple[Severity, float, float]:
        """Return (Severity, confidence, priority) for the named rule, per POC table."""
        values: dict[str, tuple[Severity, float, float]] = {
            "missing_required": (Severity.HIGH, 0.95, 0.90),
            "invalid_stage": (Severity.HIGH, 0.98, 0.95),
            "missing_account": (Severity.HIGH, 0.95, 0.90),
            "missing_contract_closed_won": (Severity.HIGH, 0.92, 0.86),
            "non_positive_amount": (Severity.MEDIUM, 0.92, 0.75),
            "numeric_outlier": (Severity.MEDIUM, 0.76, 0.57),
            "rare_combination": (Severity.LOW, 0.68, 0.36),
            "robust_outlier": (Severity.MEDIUM, 0.82, 0.60),
            "stale_open_opportunity": (Severity.HIGH, 0.90, 0.88),
            "duplicate_name": (Severity.MEDIUM, 0.80, 0.65),
            "missing_close_date": (Severity.MEDIUM, 0.85, 0.60),
            "pattern_break": (Severity.MEDIUM, 0.80, 0.70),
            "ml_anomaly": (Severity.MEDIUM, 0.70, 0.65),
            "required_missing": (Severity.HIGH, 0.95, 0.90),
            "picklist_invalid": (Severity.HIGH, 0.97, 0.93),
            "reference_missing": (Severity.HIGH, 0.93, 0.88),
            "orphan_reference": (Severity.HIGH, 0.90, 0.85),
        }
        return values[rule]


DEFAULT_POLICY = FieldPolicy()
"""Default policy instance for composition root wiring."""
