from __future__ import annotations

from typing import ClassVar

from ...domain.models import CheckRequirement
from ...domain.values import clean
from ..base import Candidate, DetectionContext, make_candidate


class OrphanReferenceDetector:
    """Schema-driven orphan / dangling reference detector.

    Detects non-empty reference values that do not point to any known parent Id
    in this scan (self-refs from own Ids, cross-refs from supplied related_ids).

    id='orphan_reference'; requirement=None.

    NOTE: assumes consistent 15- vs 18-char Salesforce Ids across the scan
    (Id normalization is a later hardening item per blueprint doc 15).
    """

    id: str = "orphan_reference"
    requirement: ClassVar[CheckRequirement | None] = None

    def detect(self, ctx: DetectionContext) -> list[Candidate]:
        if ctx.schema is None:
            return []
        refs = ctx.schema.reference_fields()
        if not refs:
            return []
        own_ids = frozenset(clean(r.get("Id")) for r in ctx.records if clean(r.get("Id")))
        candidates: list[Candidate] = []
        for fd in refs:
            if fd.name not in ctx.fields:
                continue
            # Build known parent Id sets
            known_sets: list[frozenset[str]] = []
            for t in fd.reference_to:
                if t == ctx.object_name:
                    known_sets.append(own_ids)
                elif t in ctx.related_ids:
                    known_sets.append(ctx.related_ids[t])
            if not known_sets:
                # cannot verify this field -> skip (coverage: no parent data)
                continue
            valid = frozenset().union(*known_sets)
            for record in ctx.records:
                if not clean(record.get("Id")):
                    continue
                val = clean(record.get(fd.name))
                if not val:
                    continue  # empty is required/referential, not orphan
                if val not in valid:
                    candidates.append(
                        make_candidate(
                            ctx,
                            record,
                            rule="orphan_reference",
                            title=f"Orphan reference in {fd.name}",
                            field_name=fd.name,
                            evidence={
                                "field": fd.name,
                                "reference_to": list(fd.reference_to),
                                "observed_value": val,
                                "checked_against": list(fd.reference_to),
                                "known_parent_count": len(valid),
                                "why": f"{fd.name} points to a record ({val}) that is not present in the scanned data — a possible orphan / dangling reference.",
                            },
                        )
                    )
        return candidates
