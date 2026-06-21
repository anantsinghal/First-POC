from __future__ import annotations

from typing import ClassVar

from ...domain.models import CheckRequirement
from ...domain.values import clean
from ..base import Candidate, DetectionContext, make_candidate


class MissingCloseDateDetector:
    """Completeness detector for Opportunity missing CloseDate. id='missing_close_date'."""

    id: str = "missing_close_date"
    requirement: ClassVar[CheckRequirement | None] = CheckRequirement("Missing-Close-Date check", ("CloseDate",), "opportunity")

    def detect(self, ctx: DetectionContext) -> list[Candidate]:
        """Only for Opportunity with CloseDate field present; emit on empty CloseDate values (with Id)."""
        if not (ctx.is_opportunity() and ctx.has("CloseDate")):
            return []
        candidates: list[Candidate] = []
        for record in ctx.records:
            if not clean(record.get("Id")):
                continue
            if not clean(record.get("CloseDate")):
                candidates.append(
                    make_candidate(
                        ctx,
                        record,
                        rule="missing_close_date",
                        title="Opportunity has no Close Date",
                        field_name="CloseDate",
                        evidence={
                            "observed_value": None,
                            "why": "Opportunity has no CloseDate; forecasting and stage SLAs cannot be evaluated.",
                        },
                    )
                )
        return candidates
