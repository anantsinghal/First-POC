from __future__ import annotations

from typing import Any, ClassVar

from ...domain.models import CheckRequirement
from ...domain.values import clean, parse_date, parse_float
from ..base import Candidate, DetectionContext, make_candidate

try:
    import numpy as np
    from sklearn.ensemble import IsolationForest
    _SKLEARN_AVAILABLE = True
except Exception:
    _SKLEARN_AVAILABLE = False


class IsolationForestDetector:
    """Isolation Forest ML anomaly detector (guarded; returns [] if sklearn unavailable)."""

    id: str = "ml_anomaly"
    requirement: ClassVar[CheckRequirement | None] = None

    def __init__(
        self,
        *,
        min_records: int = 50,
        contamination: float = 0.02,
        random_state: int = 42,
        categorical_max_distinct: int = 8,
        n_estimators: int = 200,
    ) -> None:
        self.min_records = min_records
        self.contamination = contamination
        self.random_state = random_state
        self.categorical_max_distinct = categorical_max_distinct
        self.n_estimators = n_estimators

    def detect(self, ctx: DetectionContext) -> list[Candidate]:
        if not _SKLEARN_AVAILABLE:
            return []

        records = [r for r in ctx.records if clean(r.get("Id"))]
        if len(records) < self.min_records:
            return []

        # Feature engineering
        feature_names: list[str] = []
        feature_matrix: list[list[float]] = []

        n = len(records)
        if n == 0:
            return []

        # Precompute for each field: non_empty values, null_rate, is_numeric, is_date, distinct etc.
        for field in ctx.fields:
            if field == "Id":
                continue
            raw_vals = [rec.get(field) for rec in records]
            non_empty_raw = [v for v in raw_vals if clean(v)]
            null_rate = (n - len(non_empty_raw)) / n if n > 0 else 0.0

            # Try numeric
            num_vals: list[float] = []
            for v in non_empty_raw:
                pf = parse_float(v)
                if pf is not None:
                    num_vals.append(pf)
            num_rate = len(num_vals) / len(non_empty_raw) if non_empty_raw else 0.0

            if num_rate >= 0.6 and len(num_vals) >= 2:
                # numeric feature
                median = float(np.median(num_vals))
                col = []
                for v in raw_vals:
                    pf = parse_float(v)
                    col.append(pf if pf is not None else median)
                feature_names.append(field)
                feature_matrix.append(col)
                continue

            # Try date
            date_vals: list[int] = []
            for v in non_empty_raw:
                pd = parse_date(v)
                if pd is not None:
                    date_vals.append(pd.toordinal())
            date_rate = len(date_vals) / len(non_empty_raw) if non_empty_raw else 0.0

            if date_rate >= 0.6 and len(date_vals) >= 2:
                median = float(np.median(date_vals))
                col = []
                for v in raw_vals:
                    pd = parse_date(v)
                    col.append(float(pd.toordinal()) if pd is not None else median)
                feature_names.append(f"{field}__ordinal")
                feature_matrix.append(col)
                continue

            # Presence boolean (if varying presence and not already used as num/date)
            if 0 < null_rate < 1.0:
                col = [1.0 if clean(v) else 0.0 for v in raw_vals]
                feature_names.append(f"has_{field}")
                feature_matrix.append(col)
                continue

            # Low-cardinality one-hot (non num/date fields, excluding Id, Name)
            if field in ("Id", "Name"):
                continue
            if null_rate == 0 or null_rate == 1:
                continue
            vals = [clean(v) for v in raw_vals if clean(v)]
            distinct = set(vals)
            if 1 < len(distinct) <= self.categorical_max_distinct:
                for val in sorted(distinct):
                    col = [1.0 if clean(v) == val else 0.0 for v in raw_vals]
                    feature_names.append(f"{field}={val}")
                    feature_matrix.append(col)

        if len(feature_matrix) < 2:
            return []

        # Transpose to rows x features
        X = np.array([list(col) for col in zip(*feature_matrix)], dtype=float)

        # Fit
        model = IsolationForest(
            n_estimators=self.n_estimators,
            contamination=self.contamination,
            random_state=self.random_state,
        )
        model.fit(X)
        preds = model.predict(X)  # -1 outlier, 1 inlier
        scores = model.score_samples(X)  # lower = more anomalous

        # Precompute mean/std per feature for explainability
        means = np.mean(X, axis=0)
        stds = np.std(X, axis=0)
        stds = np.where(stds == 0, 1.0, stds)  # avoid div0

        # Find flagged
        flagged_indices = [i for i, p in enumerate(preds) if p == -1]
        if not flagged_indices:
            return []

        # Compute threshold for normalization (use offset_ if available, else quantile on scores)
        if hasattr(model, "offset_"):
            threshold = float(model.offset_)
        else:
            # fallback: approximate the cutoff
            threshold = float(np.percentile(scores, 100 * (1 - self.contamination)))

        # Normalize strength over flagged
        flagged_scores = [scores[i] for i in flagged_indices]
        if flagged_scores:
            s_min = min(flagged_scores)
            s_max = max(flagged_scores)
            # strength = (threshold - s) normalized, assuming lower s more anomalous
            # but to match spec "against the flagged range"
            range_min = min((threshold - fs) for fs in flagged_scores) if threshold is not None else 0
            range_max = max((threshold - fs) for fs in flagged_scores) if threshold is not None else 1
            range_span = range_max - range_min if range_max > range_min else 1.0
        else:
            range_min, range_span = 0.0, 1.0

        candidates: list[Candidate] = []
        for i in flagged_indices:
            rec = records[i]
            s = scores[i]
            # strength
            str_val = threshold - s
            strength = (str_val - range_min) / range_span if range_span > 0 else 0.0
            confidence = round(min(0.95, 0.6 + 0.35 * max(0.0, strength)), 2)

            # top contributors by |z|
            z_scores = (X[i] - means) / stds
            contribs = []
            for j, fname in enumerate(feature_names):
                z = float(z_scores[j])
                contribs.append({"feature": fname, "value": round(float(X[i, j]), 3), "z": round(z, 2)})
            contribs.sort(key=lambda x: abs(x["z"]), reverse=True)
            top = contribs[:3]

            # why
            top_str = ", ".join(f"{c['feature']} (z={c['z']})" for c in top)
            why = f"This record is unusual vs. {len(records)} similar records; most unusual: {top_str}. Human review required."

            evidence: dict[str, Any] = {
                "method": "Isolation Forest",
                "anomaly_score": round(float(s), 4),
                "n_features": len(feature_names),
                "n_records": len(records),
                "contamination": self.contamination,
                "random_state": self.random_state,
                "top_contributors": top,
                "why": why,
            }

            cand = make_candidate(
                ctx,
                rec,
                rule="ml_anomaly",
                title="Statistically unusual record (ML)",
                field_name=None,
                evidence=evidence,
                confidence=confidence,
            )
            candidates.append(cand)

        return candidates
