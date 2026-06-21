from __future__ import annotations

from dataclasses import dataclass

from ..domain.enums import Severity


VALID_OPPORTUNITY_STAGES: frozenset[str] = frozenset({
    "Prospecting", "Qualification", "Needs Analysis", "Value Proposition",
    "Id. Decision Makers", "Perception Analysis", "Proposal/Price Quote",
    "Negotiation/Review", "Closed Won", "Closed Lost", "Proposal",
})
"""Allowed Opportunity StageName values per policy."""


@dataclass(frozen=True)
class FieldPolicy:
    """Policy defining expected fields, valid stages, and rule severity/score mapping."""

    def expected_fields(self, object_name: str) -> set[str]:
        """Return minimal expected fields for object; Id always, plus Opportunity core fields."""
        expected: set[str] = {"Id"}
        if object_name.lower() == "opportunity":
            expected |= {"Name", "StageName", "Amount", "AccountId", "Contract__c"}
        return expected

    def valid_stages(self) -> frozenset[str]:
        """Return the allowed stages for Opportunity objects."""
        return VALID_OPPORTUNITY_STAGES

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
        }
        return values[rule]


DEFAULT_POLICY = FieldPolicy()
"""Default policy instance for composition root wiring."""
