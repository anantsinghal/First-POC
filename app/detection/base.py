from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from ..domain.models import Candidate, Profile, Record
from ..domain.schema import ObjectSchema
from ..domain.values import clean, display_name
from ..policy.field_policy import FieldPolicy


@dataclass(frozen=True)
class DetectionContext:
    """Context passed to detectors: data slice + policy + optional profile + optional schema (for metadata-driven engines)."""

    object_name: str
    records: list[Record]
    fields: list[str]
    policy: FieldPolicy
    profile: Profile | None = None
    schema: ObjectSchema | None = None
    related_ids: dict[str, frozenset[str]] = field(default_factory=dict)
    related_records: dict[str, list[Record]] = field(default_factory=dict)

    @property
    def field_set(self) -> set[str]:
        """Set of available field names for fast lookup."""
        return set(self.fields)

    def has(self, name: str) -> bool:
        """Whether the given field name is present in the input."""
        return name in self.field_set


@runtime_checkable
class Detector(Protocol):
    """Protocol for all detectors. id is class attr; detect produces Candidates."""

    id: str

    def detect(self, ctx: DetectionContext) -> list[Candidate]:
        """Return list of Candidate findings for this detector."""
        ...


def make_candidate(
    ctx: DetectionContext,
    record: dict[str, str],
    rule: str,
    title: str,
    field_name: str | None,
    evidence: dict[str, Any],
    *,
    confidence: float | None = None,
    priority: float | None = None,
) -> Candidate:
    """Build Candidate using policy for severity/conf/pri; supports per-finding overrides for patterns/ML (backward compatible)."""
    severity, def_conf, def_pri = ctx.policy.severity_for(rule)
    conf = confidence if confidence is not None else def_conf
    pri = priority if priority is not None else def_pri
    return Candidate(
        object_name=ctx.object_name,
        record_id=clean(record.get("Id")),
        record_name=display_name(record),
        finding_type=rule,
        field_name=field_name,
        severity=severity,
        confidence=conf,
        priority=pri,
        title=title,
        evidence=evidence,
    )
