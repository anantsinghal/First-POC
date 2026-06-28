from __future__ import annotations

from collections.abc import Iterable

from ..domain.models import CheckRequirement
from .base import Candidate, DetectionContext, Detector
from .reconcile import reconcile
from .ml.isolation_forest import IsolationForestDetector
from .patterns.association import AssociationPatternDetector
from .rules.duplicates import DuplicateNameDetector
from .rules.interpreter import RuleInterpreterDetector
from .schema_driven.picklist_validity import PicklistValidityDetector
from .graph.orphan_reference import OrphanReferenceDetector
from .graph.missing_children import MissingChildrenDetector
from .schema_driven.format_validity import FormatValidityDetector
from .schema_driven.referential_integrity import ReferentialIntegrityDetector
from .schema_driven.required_field import RequiredFieldDetector
from .statistical.iqr_outlier import IqrOutlierDetector
from .statistical.mad_outlier import MadOutlierDetector
from .statistical.rare_combination import RareCombinationDetector

from ..policy.rules import load_rule_catalog


class DetectorRegistry:
    """Ordered registry of detectors; detect runs them in registration order."""

    def __init__(self, detectors: Iterable[Detector] = ()) -> None:
        """Initialize with optional initial list of detectors."""
        self._detectors: list[Detector] = list(detectors)

    def register(self, detector: Detector) -> DetectorRegistry:
        """Append a detector and return self for chaining."""
        self._detectors.append(detector)
        return self

    @property
    def detectors(self) -> tuple[Detector, ...]:
        """Read-only tuple of registered detectors (in order)."""
        return tuple(self._detectors)

    def detect(self, ctx: DetectionContext) -> list[Candidate]:
        """Run all detectors, concatenate, then reconcile duplicate/equivalent findings."""
        results: list[Candidate] = []
        for det in self._detectors:
            results.extend(det.detect(ctx))
        return reconcile(results)

    def requirements(self) -> list[CheckRequirement]:
        """Return declared requirements for detectors that have them (in registration order).
        Also collects from detectors that expose coverage_requirements() (e.g. RuleInterpreterDetector).
        """
        reqs: list[CheckRequirement] = []
        for d in self._detectors:
            cr = getattr(d, "coverage_requirements", None)
            if callable(cr):
                reqs.extend(cr())
            elif getattr(d, "requirement", None) is not None:
                reqs.append(d.requirement)
        return reqs


def build_default_registry() -> DetectorRegistry:
    """Return registry with the 7 POC + 3 new detectors appended (preserving original order)."""
    return DetectorRegistry([
        IqrOutlierDetector(),
        MadOutlierDetector(),
        RareCombinationDetector(),
        DuplicateNameDetector(),
        AssociationPatternDetector(),
        IsolationForestDetector(),
        RequiredFieldDetector(),
        PicklistValidityDetector(),
        ReferentialIntegrityDetector(),
        RuleInterpreterDetector(load_rule_catalog()),
        OrphanReferenceDetector(),
        FormatValidityDetector(),
        MissingChildrenDetector(),
    ])
