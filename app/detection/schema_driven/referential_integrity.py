from __future__ import annotations

from typing import ClassVar

from ...domain.models import CheckRequirement
from ...domain.values import clean
from ..base import Candidate, DetectionContext, make_candidate


class ReferentialIntegrityDetector:
    """Schema-driven referential integrity for required references (no-op if no schema). id='reference_missing'."""

    id: str = "reference_missing"
    requirement: ClassVar[CheckRequirement | None] = None

    def detect(self, ctx: DetectionContext) -> list[Candidate]:
        if ctx.schema is None:
            return []
        candidates: list[Candidate] = []
        for fd in ctx.schema.reference_fields():
            if fd.name not in ctx.fields or not fd.required:
                continue
            for record in ctx.records:
                if not clean(record.get("Id")):
                    continue
                if not clean(record.get(fd.name)):
                    candidates.append(
                        make_candidate(
                            ctx,
                            record,
                            rule="reference_missing",
                            title=f"Missing required reference {fd.name}",
                            field_name=fd.name,
                            evidence={
                                "field": fd.name,
                                "reference_to": list(fd.reference_to),
                                "observed_value": None,
                                "why": f"Required lookup {fd.name} (to {', '.join(fd.reference_to) or 'parent'}) is empty.",
                            },
                        )
                    )
        return candidates
