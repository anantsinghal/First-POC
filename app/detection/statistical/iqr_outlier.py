from __future__ import annotations

from ...domain.values import numeric_values, parse_float
from ..base import Candidate, DetectionContext, make_candidate


class IqrOutlierDetector:
    """Numeric IQR outlier detector. id='numeric_outlier'. Uses exact baseline quartile calc."""

    id: str = "numeric_outlier"

    def detect(self, ctx: DetectionContext) -> list[Candidate]:
        """For every field, if >=8 numeric values compute IQR 1.5 fences and flag outliers."""
        candidates: list[Candidate] = []
        for field in ctx.fields:
            values = sorted(numeric_values(ctx.records, field))
            if len(values) < 8:
                continue
            q1 = values[len(values) // 4]
            q3 = values[(len(values) * 3) // 4]
            iqr = q3 - q1
            if iqr <= 0:
                continue
            lower = q1 - 1.5 * iqr
            upper = q3 + 1.5 * iqr
            for record in ctx.records:
                v = parse_float(record.get(field))
                if v is None:
                    continue
                if v < lower or v > upper:
                    candidates.append(
                        make_candidate(
                            ctx,
                            record,
                            rule="numeric_outlier",
                            title=f"Unusual {field} value",
                            field_name=field,
                            evidence={
                                "observed_value": v,
                                "method": "IQR",
                                "lower_bound": round(lower, 2),
                                "upper_bound": round(upper, 2),
                                "why": "The value falls outside the 1.5×IQR range of this scan.",
                            },
                        )
                    )
        return candidates
