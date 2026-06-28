from __future__ import annotations

from .base import Candidate

# Equivalence groups: finding types that express the SAME underlying issue on a given
# (object, record, field). Within a group, EARLIER entries have higher precedence and are
# kept; later entries are dropped when a higher-precedence sibling targets the same
# record+field. Metadata/schema-derived checks (auto-derived from the live org) outrank
# rule-catalog duplicates of the same concept.
#
# Types NOT listed here are always kept — so genuine corroboration (e.g. numeric_outlier
# AND robust_outlier both flagging an Amount) is preserved.
EQUIVALENCE_GROUPS: tuple[tuple[str, ...], ...] = (
    ("picklist_invalid", "invalid_stage"),
    ("reference_missing", "missing_account"),
    ("required_missing", "missing_required"),
)


def reconcile(candidates: list[Candidate]) -> list[Candidate]:
    """Drop lower-precedence findings that duplicate a higher-precedence finding of the
    same equivalence group on the same (object, record, field).

    No-op for any finding type not in an equivalence group, and for keys where only one
    group member is present (so CSV/no-schema scans are unaffected).
    """
    type_group: dict[str, int] = {}
    type_rank: dict[str, int] = {}
    for group_index, group in enumerate(EQUIVALENCE_GROUPS):
        for rank, finding_type in enumerate(group):
            type_group[finding_type] = group_index
            type_rank[finding_type] = rank

    def key_for(c: Candidate) -> tuple[str, str, str | None, int]:
        return (c.object_name, c.record_id, c.field_name, type_group[c.finding_type])

    best_rank: dict[tuple[str, str, str | None, int], int] = {}
    for c in candidates:
        if c.finding_type in type_group:
            k = key_for(c)
            best_rank[k] = min(type_rank[c.finding_type], best_rank.get(k, 1 << 30))

    reconciled: list[Candidate] = []
    for c in candidates:
        if c.finding_type in type_group and type_rank[c.finding_type] != best_rank[key_for(c)]:
            continue  # superseded by a higher-precedence equivalent finding
        reconciled.append(c)
    return reconciled
