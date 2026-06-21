from __future__ import annotations

from ..domain.enums import CoverageGrade
from ..domain.models import Coverage, Record
from ..policy.field_policy import FieldPolicy


class CoverageGrader:
    """Coverage grader that computes grade + suppressed checks using injected policy (policy-driven expected fields)."""

    def __init__(self, policy: FieldPolicy) -> None:
        """Inject the FieldPolicy for expected_fields and object-specific rules."""
        self.policy = policy

    def grade(self, object_name: str, records: list[Record], fields: list[str]) -> Coverage:
        """Return Coverage value object; logic mirrors baseline calculate_coverage but via policy."""
        expected = self.policy.expected_fields(object_name)
        missing = sorted(expected - set(fields))
        suppressed: list[str] = []
        if "Id" in missing:
            suppressed.append("all record-level checks: Id is absent")
        if object_name.lower() == "opportunity":
            if "StageName" in missing:
                suppressed.append("invalid-stage and closed-won rules: StageName is absent")
            if "Contract__c" in missing:
                suppressed.append("missing-contract rule: Contract__c is absent")
            if "AccountId" in missing:
                suppressed.append("missing-account rule: AccountId is absent")
        if not records or "Id" in missing:
            grade, reason = CoverageGrade.BLOCKED, "No trustworthy record-level scan: input is empty or has no Id column."
        elif missing:
            grade, reason = CoverageGrade.PARTIAL, "Some expected fields are unavailable; dependent checks were suppressed."
        else:
            grade, reason = CoverageGrade.GOOD, "Selected POC fields were present in the supplied file."
        return Coverage(
            object_name=object_name,
            observed_records=len(records),
            queried_fields=fields,
            missing_fields=missing,
            suppressed_checks=suppressed,
            grade=grade,
            reason=reason,
        )
