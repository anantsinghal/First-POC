from __future__ import annotations

from typing import ClassVar

from ...domain.models import CheckRequirement
from ...domain.schema import FieldType
from ...domain.values import clean
from ..base import Candidate, DetectionContext, make_candidate


class RequiredFieldDetector:
    """Schema-driven required field check (no-op if no schema). id='required_missing'."""

    id: str = "required_missing"
    requirement: ClassVar[CheckRequirement | None] = None

    def detect(self, ctx: DetectionContext) -> list[Candidate]:
        if ctx.schema is None:
            return []
        candidates: list[Candidate] = []
        for fd in ctx.schema.required_fields():
            if fd.name not in ctx.fields or fd.data_type == FieldType.REFERENCE:
                continue
            for record in ctx.records:
                if not clean(record.get("Id")):
                    continue
                if not clean(record.get(fd.name)):
                    candidates.append(
                        make_candidate(
                            ctx,
                            record,
                            rule="required_missing",
                            title=f"Required field {fd.name} is empty",
                            field_name=fd.name,
                            evidence={
                                "rule": "schema-required (metadata)",
                                "field": fd.name,
                                "data_type": fd.data_type.value,
                                "observed_value": None,
                                "why": f"{fd.name} is required by the org metadata but is empty.",
                            },
                        )
                    )
        return candidates
