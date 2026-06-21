from __future__ import annotations

from collections.abc import Iterable

from .base import Candidate, DetectionContext, Detector
from .rules.opportunity import (
    ClosedWonContractDetector,
    InvalidStageDetector,
    MissingAccountDetector,
    NonPositiveAmountDetector,
)
from .rules.required_fields import RequiredFieldsDetector
from .statistical.iqr_outlier import IqrOutlierDetector
from .statistical.rare_combination import RareCombinationDetector


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
        """Run all detectors in order and concatenate their Candidate lists."""
        results: list[Candidate] = []
        for det in self._detectors:
            results.extend(det.detect(ctx))
        return results


def build_default_registry() -> DetectorRegistry:
    """Return registry with the 7 POC detectors in the exact baseline order."""
    return DetectorRegistry([
        RequiredFieldsDetector(),
        InvalidStageDetector(),
        MissingAccountDetector(),
        ClosedWonContractDetector(),
        NonPositiveAmountDetector(),
        IqrOutlierDetector(),
        RareCombinationDetector(),
    ])
