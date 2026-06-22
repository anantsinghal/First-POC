from __future__ import annotations

from typing import ClassVar

from ...domain.models import CheckRequirement
from ...domain.values import clean
from ..base import Candidate, DetectionContext, make_candidate


class PicklistValidityDetector:
    """Schema-driven picklist validity check (no-op if no schema). id='picklist_invalid'."""

    id: str = "picklist_invalid"
    requirement: ClassVar[CheckRequirement | None] = None

    def detect(self, ctx: DetectionContext) -> list[Candidate]:
        if ctx.schema is None:
            return []
        candidates: list[Candidate] = []
        for fd in ctx.schema.picklist_fields():
            if fd.name not in ctx.fields or not fd.picklist_values:
                continue
            for record in ctx.records:
                if not clean(record.get("Id")):
                    continue
                value = clean(record.get(fd.name))
                if value and value not in fd.picklist_values:
                    candidates.append(
                        make_candidate(
                            ctx,
                            record,
                            rule="picklist_invalid",
                            title=f"Invalid {fd.name} value",
                            field_name=fd.name,
                            evidence={
                                "field": fd.name,
                                "observed_value": value,
                                "allowed_values": sorted(fd.picklist_values)[:50],
                                "why": f"{fd.name}={value} is not an active picklist value in the org metadata.",
                            },
                        )
                    )
        return candidates
