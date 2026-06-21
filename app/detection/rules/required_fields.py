from __future__ import annotations

from typing import ClassVar

from ...domain.models import CheckRequirement
from ...domain.values import clean
from ..base import Candidate, DetectionContext, make_candidate


class RequiredFieldsDetector:
    """Detector for missing required fields (default: Name). id='missing_required'."""

    id: str = "missing_required"
    requirement: ClassVar[CheckRequirement | None] = CheckRequirement("Required-field check", ("Name",), None)

    def __init__(self, required: tuple[str, ...] = ("Name",)) -> None:
        """Allow overriding the tuple of required field names."""
        self.required = required

    def detect(self, ctx: DetectionContext) -> list[Candidate]:
        """Emit for each missing required (that is present in fields) on records that have Id."""
        candidates: list[Candidate] = []
        for record in ctx.records:
            if not clean(record.get("Id")):
                continue
            for name in self.required:
                if ctx.has(name) and not clean(record.get(name)):
                    candidates.append(
                        make_candidate(
                            ctx,
                            record,
                            rule="missing_required",
                            title=f"Missing required {name}",
                            field_name=name,
                            evidence={
                                "rule": "POC required-field check",
                                "observed_value": None,
                                "why": f"{name} is empty.",
                            },
                        )
                    )
        return candidates
