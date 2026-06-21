from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

from ..domain.models import Candidate, Profile, Record
from ..domain.values import clean, display_name
from ..policy.field_policy import FieldPolicy


@dataclass(frozen=True)
class DetectionContext:
    """Context passed to detectors: data slice + policy + optional profile."""

    object_name: str
    records: list[Record]
    fields: list[str]
    policy: FieldPolicy
    profile: Profile | None = None

    @property
    def field_set(self) -> set[str]:
        """Set of available field names for fast lookup."""
        return set(self.fields)

    def has(self, name: str) -> bool:
        """Whether the given field name is present in the input."""
        return name in self.field_set

    def is_opportunity(self) -> bool:
        """True if object_name is Opportunity (case-insensitive)."""
        return self.object_name.lower() == "opportunity"


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
) -> Candidate:
    """Build Candidate using policy for severity/conf/pri; mirrors old create_candidate but no id/status."""
    severity, confidence, priority = ctx.policy.severity_for(rule)
    return Candidate(
        object_name=ctx.object_name,
        record_id=clean(record.get("Id")),
        record_name=display_name(record),
        finding_type=rule,
        field_name=field_name,
        severity=severity,
        confidence=confidence,
        priority=priority,
        title=title,
        evidence=evidence,
    )
