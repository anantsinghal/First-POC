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

    def grade(
        self,
        object_name: str,
        records: list[Record],
        fields: list[str],
        *,
        sampled: bool = False,
        record_count: int | None = None,
    ) -> Coverage:
        """Return Coverage value object; logic mirrors baseline calculate_coverage but via policy.

        ``sampled=True`` means only a prefix of the object's records was scanned (e.g. a record cap
        was hit on a large object). Such a scan is NEVER reported as complete/GOOD — it is downgraded
        to PARTIAL and annotated, so coverage can never overstate what was actually examined.

        ``record_count`` lets streaming callers supply the observed count without materialising the
        record list; when None the count is ``len(records)`` (unchanged behaviour).
        """
        observed = record_count if record_count is not None else len(records)
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
        if observed == 0 or "Id" in missing:
            grade, reason = CoverageGrade.BLOCKED, "No trustworthy record-level scan: input is empty or has no Id column."
        elif missing:
            grade, reason = CoverageGrade.PARTIAL, "Some expected fields are unavailable; dependent checks were suppressed."
        else:
            grade, reason = CoverageGrade.GOOD, "Selected POC fields were present in the supplied file."
        if sampled and grade != CoverageGrade.BLOCKED:
            # A capped/sampled scan only saw a prefix of the object — never claim completeness.
            grade = CoverageGrade.PARTIAL
            reason = (
                f"Sampled scan: only the first {observed} records were examined (record cap reached); "
                f"findings are over that sample and coverage is NOT org-complete. " + reason
            )
            suppressed.append("org-complete coverage: record cap reached (sampled scan)")
        return Coverage(
            object_name=object_name,
            observed_records=observed,
            queried_fields=fields,
            missing_fields=missing,
            suppressed_checks=suppressed,
            grade=grade,
            reason=reason,
        )
