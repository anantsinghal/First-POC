from __future__ import annotations

from dataclasses import dataclass


DETECTOR_TIERS: dict[str, int] = {
    "required_missing": 0,
    "picklist_invalid": 0,
    "reference_missing": 0,
    "invalid_format": 0,
    "rule_interpreter": 0,
    "numeric_outlier": 1,
    "robust_outlier": 1,
    "rare_combination": 1,
    "duplicate_name": 1,
    "orphan_reference": 1,
    "missing_children": 1,
    "pattern_break": 2,
    "ml_anomaly": 2,
}


@dataclass(frozen=True)
class ScanPlan:
    """Detector execution plan for one object scan."""

    object_name: str
    estimated_records: int
    included_ids: frozenset[str]
    skipped: tuple[tuple[str, str], ...]
    notes: str


@dataclass(frozen=True)
class ScanPlanner:
    """Plan detector execution tiers based on estimated scan size."""

    tier1_max_records: int = 2_000_000
    pattern_max_records: int = 500_000
    ml_max_records: int = 200_000
    columnar_profile_min_records: int = 100_000

    def use_columnar_profile(self, estimated_records: int) -> bool:
        """Whether to profile this object out-of-core (Parquet+DuckDB) rather than in memory."""
        return estimated_records >= self.columnar_profile_min_records

    def plan(self, object_name: str, estimated_records: int) -> ScanPlan:
        """Return the planned detector set and readable deferral reasons."""
        included: set[str] = set()
        skipped: list[tuple[str, str]] = []
        for detector_id, tier in DETECTOR_TIERS.items():
            if tier == 0:
                included.add(detector_id)
                continue
            if tier == 1:
                if estimated_records <= self.tier1_max_records:
                    included.add(detector_id)
                else:
                    skipped.append((
                        detector_id,
                        f"tier-1 detector deferred: {estimated_records} records exceeds {self.tier1_max_records} (needs columnar/streaming)",
                    ))
                continue
            if detector_id == "pattern_break":
                if estimated_records <= self.pattern_max_records:
                    included.add(detector_id)
                else:
                    skipped.append((
                        detector_id,
                        f"pattern mining deferred: {estimated_records} exceeds {self.pattern_max_records} (needs sampling)",
                    ))
                continue
            if detector_id == "ml_anomaly":
                if estimated_records <= self.ml_max_records:
                    included.add(detector_id)
                else:
                    skipped.append((
                        detector_id,
                        f"ML scoring deferred: {estimated_records} exceeds {self.ml_max_records} (needs sampling)",
                    ))
                continue
            included.add(detector_id)

        notes = "all detectors planned" if not skipped else f"{len(skipped)} detector(s) deferred for size"
        return ScanPlan(
            object_name=object_name,
            estimated_records=estimated_records,
            included_ids=frozenset(included),
            skipped=tuple(skipped),
            notes=notes,
        )

    def applies(self, detector_id: str, plan: ScanPlan) -> bool:
        """Return whether a detector should run under the provided plan."""
        return detector_id in plan.included_ids or detector_id not in DETECTOR_TIERS
