from __future__ import annotations

import statistics
from typing import ClassVar

from ...domain.models import CheckRequirement
from ...domain.values import numeric_values, parse_float
from ..base import Candidate, DetectionContext, make_candidate


class MadOutlierDetector:
    """MAD (median absolute deviation) modified-z-score outlier detector. Robust to skew/outliers. id='robust_outlier'."""

    id: str = "robust_outlier"
    requirement: ClassVar[CheckRequirement | None] = None  # scans whatever numeric fields exist

    def __init__(self, threshold: float = 3.5) -> None:
        """Standard modified-z cutoff (default 3.5)."""
        self.threshold = threshold

    def detect(self, ctx: DetectionContext) -> list[Candidate]:
        """For fields with >=8 values, compute MAD modified z-score and flag |mz| > threshold."""
        candidates: list[Candidate] = []
        for field in ctx.fields:
            values = numeric_values(ctx.records, field)
            if len(values) < 8:
                continue
            med = statistics.median(values)
            mad = statistics.median([abs(v - med) for v in values])
            if mad <= 0:
                continue
            for record in ctx.records:
                v = parse_float(record.get(field))
                if v is None:
                    continue
                mz = 0.6745 * (v - med) / mad
                if abs(mz) > self.threshold:
                    candidates.append(
                        make_candidate(
                            ctx,
                            record,
                            rule="robust_outlier",
                            title=f"Unusual {field} value (robust)",
                            field_name=field,
                            evidence={
                                "observed_value": v,
                                "method": "MAD modified z-score",
                                "median": round(med, 2),
                                "mad": round(mad, 2),
                                "modified_z": round(mz, 2),
                                "threshold": self.threshold,
                                "why": "The value is far from the median relative to the median absolute deviation (robust to skew).",
                            },
                        )
                    )
        return candidates
