from __future__ import annotations

from collections import Counter
from typing import ClassVar

from ...domain.models import CheckRequirement
from ...domain.values import clean
from ..base import Candidate, DetectionContext, make_candidate


class DuplicateNameDetector:
    """Duplicate detector by (case-folded) Name. id='duplicate_name'."""

    id: str = "duplicate_name"
    requirement: ClassVar[CheckRequirement | None] = CheckRequirement("Duplicate-by-Name check", ("Name",), None)

    def detect(self, ctx: DetectionContext) -> list[Candidate]:
        """Return [] unless has Name; count casefold names for Id+Name rows; emit for dups."""
        if not ctx.has("Name"):
            return []
        name_counts: Counter[str] = Counter()
        for record in ctx.records:
            if not clean(record.get("Id")):
                continue
            name = clean(record.get("Name"))
            if not name:
                continue
            name_counts[name.casefold()] += 1
        dups = {k for k, v in name_counts.items() if v > 1}
        if not dups:
            return []
        candidates: list[Candidate] = []
        for record in ctx.records:
            if not clean(record.get("Id")):
                continue
            name = clean(record.get("Name"))
            if not name:
                continue
            norm = name.casefold()
            if norm in dups:
                candidates.append(
                    make_candidate(
                        ctx,
                        record,
                        rule="duplicate_name",
                        title="Possible duplicate by Name",
                        field_name="Name",
                        evidence={
                            "observed_value": name,
                            "duplicate_count": name_counts[norm],
                            "why": "Another record in this scan shares this Name; verify these are not duplicates.",
                        },
                    )
                )
        return candidates
