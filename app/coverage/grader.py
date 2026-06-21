from __future__ import annotations

from collections.abc import Iterable

from ..domain.enums import CoverageGrade
from ..domain.models import CheckRequirement, Coverage, Record
from ..policy.field_policy import FieldPolicy


class CoverageGrader:
    """Coverage grader that computes grade + suppressed checks using injected policy (policy-driven expected fields).

    requirements (optional): list of CheckRequirement from registry to make suppressed_checks data-driven.
    """

    def __init__(self, policy: FieldPolicy, requirements: Iterable[CheckRequirement] = ()) -> None:
        """Inject the FieldPolicy ... and optional requirements (default empty for backward compat)."""
        self.policy = policy
        self.requirements = tuple(requirements)

    def grade(self, object_name: str, records: list[Record], fields: list[str]) -> Coverage:
        """Return Coverage value object; logic mirrors baseline calculate_coverage but via policy."""
        expected = self.policy.expected_fields(object_name)
        missing = sorted(expected - set(fields))
        field_set = set(fields)
        suppressed: list[str] = []
        if "Id" in missing:
            suppressed.append("all record-level checks: Id is absent")
        for req in self.requirements:
            if req.object_scope is not None and req.object_scope != object_name.lower():
                continue
            missing_req = [f for f in req.requires if f not in field_set]
            if missing_req:
                suppressed.append(f"{req.label}: needs {', '.join(missing_req)}")
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
