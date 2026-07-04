from __future__ import annotations

import statistics
from collections import defaultdict
from dataclasses import dataclass
from typing import Any, ClassVar

from ...domain.models import CheckRequirement
from ...domain.values import clean, parse_date, parse_float
from ..base import Candidate, DetectionContext, make_candidate

try:
    import numpy as np
    from sklearn.ensemble import IsolationForest

    _SKLEARN_AVAILABLE = True
except Exception:
    np = None
    IsolationForest = None
    _SKLEARN_AVAILABLE = False


@dataclass(frozen=True)
class FeatureColumnSpec:
    kind: str
    source_field: str
    feature_name: str
    median: float | None = None
    onehot_value: str | None = None


@dataclass(frozen=True)
class FeatureSpec:
    columns: tuple[FeatureColumnSpec, ...]

    @property
    def feature_names(self) -> list[str]:
        return [column.feature_name for column in self.columns]


def build_feature_spec(
    records: list[dict[str, str]],
    fields: list[str],
    *,
    categorical_max_distinct: int = 8,
) -> FeatureSpec:
    """Build the feature spec using the exact field-selection rules from the detector."""
    columns: list[FeatureColumnSpec] = []
    n = len(records)
    for field in fields:
        if field == "Id":
            continue
        raw_vals = [rec.get(field) for rec in records]
        non_empty_raw = [v for v in raw_vals if clean(v)]
        null_rate = (n - len(non_empty_raw)) / n if n > 0 else 0.0

        num_vals = [pf for v in non_empty_raw if (pf := parse_float(v)) is not None]
        num_rate = len(num_vals) / len(non_empty_raw) if non_empty_raw else 0.0
        if num_rate >= 0.6 and len(num_vals) >= 2:
            columns.append(
                FeatureColumnSpec(
                    kind="numeric",
                    source_field=field,
                    feature_name=field,
                    median=float(statistics.median(num_vals)),
                )
            )
            continue

        date_vals = [pd.toordinal() for v in non_empty_raw if (pd := parse_date(v)) is not None]
        date_rate = len(date_vals) / len(non_empty_raw) if non_empty_raw else 0.0
        if date_rate >= 0.6 and len(date_vals) >= 2:
            columns.append(
                FeatureColumnSpec(
                    kind="date",
                    source_field=field,
                    feature_name=f"{field}__ordinal",
                    median=float(statistics.median(date_vals)),
                )
            )
            continue

        if 0 < null_rate < 1.0:
            columns.append(
                FeatureColumnSpec(
                    kind="presence",
                    source_field=field,
                    feature_name=f"has_{field}",
                )
            )
            continue

        if field == "Name":
            continue
        distinct = {clean(v) for v in raw_vals if clean(v)}
        if 1 < len(distinct) <= categorical_max_distinct:
            for val in sorted(distinct):
                columns.append(
                    FeatureColumnSpec(
                        kind="onehot",
                        source_field=field,
                        feature_name=f"{field}={val}",
                        onehot_value=val,
                    )
                )
    return FeatureSpec(columns=tuple(columns))


def vectorize(records: list[dict[str, str]], spec: FeatureSpec) -> "np.ndarray":
    """Apply a frozen feature spec to a record batch."""
    if np is None:
        raise RuntimeError("numpy not installed")
    rows: list[list[float]] = []
    for record in records:
        row: list[float] = []
        for column in spec.columns:
            raw = record.get(column.source_field)
            if column.kind == "numeric":
                parsed = parse_float(raw)
                row.append(float(parsed) if parsed is not None else float(column.median or 0.0))
                continue
            if column.kind == "date":
                parsed_date = parse_date(raw)
                row.append(
                    float(parsed_date.toordinal()) if parsed_date is not None else float(column.median or 0.0)
                )
                continue
            if column.kind == "presence":
                row.append(1.0 if clean(raw) else 0.0)
                continue
            row.append(1.0 if clean(raw) == column.onehot_value else 0.0)
        rows.append(row)
    return np.array(rows, dtype=float)


class StreamingIsolationForest:
    def __init__(
        self,
        *,
        contamination: float = 0.02,
        random_state: int = 42,
        n_estimators: int = 200,
        min_sample: int = 50,
    ) -> None:
        self.contamination = contamination
        self.random_state = random_state
        self.n_estimators = n_estimators
        self.min_sample = min_sample
        self.spec: FeatureSpec | None = None
        self.sample_size = 0
        self._model = None
        self._means = None
        self._stds = None
        self._threshold = 0.0

    def fit(self, sample_records: list[dict[str, str]], fields: list[str]) -> bool:
        if not _SKLEARN_AVAILABLE or len(sample_records) < self.min_sample:
            return False
        spec = build_feature_spec(sample_records, fields)
        if len(spec.columns) < 2:
            return False
        X = vectorize(sample_records, spec)
        if X.ndim != 2 or X.shape[0] < self.min_sample or X.shape[1] < 2:
            return False

        model = IsolationForest(
            n_estimators=self.n_estimators,
            contamination=self.contamination,
            random_state=self.random_state,
        )
        model.fit(X)

        stds = np.std(X, axis=0)
        self.spec = spec
        self.sample_size = int(X.shape[0])
        self._model = model
        self._means = np.mean(X, axis=0)
        self._stds = np.where(stds == 0, 1.0, stds)
        self._threshold = float(getattr(model, "offset_", 0.0))
        return True

    def score_batch(self, records: list[dict[str, str]]) -> list[tuple[int, float, list[dict]]]:
        if not records or self.spec is None or self._model is None or self._means is None or self._stds is None:
            return []
        X = vectorize(records, self.spec)
        preds = self._model.predict(X)
        scores = self._model.score_samples(X)
        flagged: list[tuple[int, float, list[dict]]] = []
        for idx, pred in enumerate(preds):
            if pred != -1:
                continue
            z_scores = (X[idx] - self._means) / self._stds
            contribs = [
                {"feature": name, "value": round(float(X[idx, j]), 3), "z": round(float(z_scores[j]), 2)}
                for j, name in enumerate(self.spec.feature_names)
            ]
            contribs.sort(key=lambda c: abs(c["z"]), reverse=True)
            flagged.append((idx, float(scores[idx]), contribs[:3]))
        return flagged


class IsolationForestDetector:
    """Isolation Forest ML anomaly detector (guarded; returns [] if sklearn unavailable).

    Cohort-aware: when a schema picklist field (or a configured cohort field) is present
    and a cohort has >= min_records, records are scored RELATIVE TO THEIR COHORT (e.g. a
    large Amount normal for Enterprise is not flagged when compared to other Enterprise
    records). Records in cohorts too small to model fall back to a whole-object model, and
    when there is no usable cohort field the detector behaves exactly as a single
    whole-object model (backward compatible).
    """

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
        cohort_fields: tuple[str, ...] | None = None,
        max_cohort_cardinality: int = 8,
    ) -> None:
        self.min_records = min_records
        self.contamination = contamination
        self.random_state = random_state
        self.categorical_max_distinct = categorical_max_distinct
        self.n_estimators = n_estimators
        self.cohort_fields = cohort_fields
        self.max_cohort_cardinality = max_cohort_cardinality

    def detect(self, ctx: DetectionContext) -> list[Candidate]:
        if not _SKLEARN_AVAILABLE:
            return []
        records = [r for r in ctx.records if clean(r.get("Id"))]
        if len(records) < self.min_records:
            return []

        feature_names, matrix = self._build_features(ctx, records)
        if len(feature_names) < 2:
            return []
        X = np.array([list(col) for col in zip(*matrix)], dtype=float)

        cohort_fields = self._select_cohort_fields(ctx, records)
        groups: dict[str, list[int]] = defaultdict(list)
        for i, rec in enumerate(records):
            groups[self._cohort_key(rec, cohort_fields)].append(i)

        cohort_field = cohort_fields[0] if cohort_fields else None
        candidates: list[Candidate] = []
        small_idxs: list[int] = []
        for key, idxs in groups.items():
            if len(idxs) >= self.min_records:
                candidates.extend(
                    self._score_subset(
                        ctx, records, X, feature_names,
                        score_idxs=idxs, train_idxs=idxs,
                        cohort_field=cohort_field, cohort_value=(key if cohort_field else None),
                    )
                )
            else:
                small_idxs.extend(idxs)

        if small_idxs and len(records) >= self.min_records:
            all_idxs = list(range(len(records)))
            candidates.extend(
                self._score_subset(
                    ctx, records, X, feature_names,
                    score_idxs=small_idxs, train_idxs=all_idxs,
                    cohort_field=None, cohort_value=None,
                )
            )
        return candidates

    def _build_features(
        self, ctx: DetectionContext, records: list[dict[str, str]]
    ) -> tuple[list[str], list[list[float]]]:
        """Build (feature_names, columns) over all records. Numeric / date-ordinal / presence / one-hot."""
        spec = build_feature_spec(
            records,
            ctx.fields,
            categorical_max_distinct=self.categorical_max_distinct,
        )
        X = vectorize(records, spec)
        return spec.feature_names, X.T.tolist()

    def _select_cohort_fields(self, ctx: DetectionContext, records: list[dict[str, str]]) -> list[str]:
        """Configured cohort fields, else the first schema picklist field with sensible cardinality."""
        if self.cohort_fields is not None:
            return [f for f in self.cohort_fields if f in ctx.fields]
        if ctx.schema is None:
            return []
        for fd in ctx.schema.picklist_fields():
            if fd.name not in ctx.fields:
                continue
            distinct = {clean(r.get(fd.name)) for r in records if clean(r.get(fd.name))}
            if 2 <= len(distinct) <= self.max_cohort_cardinality:
                return [fd.name]
        return []

    @staticmethod
    def _cohort_key(record: dict[str, str], cohort_fields: list[str]) -> str:
        """Cohort key for a record ('' when no cohort fields -> single whole-object group)."""
        if not cohort_fields:
            return ""
        return " | ".join(f"{f}={clean(record.get(f))}" for f in cohort_fields)

    def _score_subset(
        self,
        ctx: DetectionContext,
        records: list[dict[str, str]],
        X: "np.ndarray",
        feature_names: list[str],
        *,
        score_idxs: list[int],
        train_idxs: list[int],
        cohort_field: str | None,
        cohort_value: str | None,
    ) -> list[Candidate]:
        """Fit Isolation Forest on train_idxs and flag outliers among score_idxs."""
        X_train = X[train_idxs]
        model = IsolationForest(
            n_estimators=self.n_estimators,
            contamination=self.contamination,
            random_state=self.random_state,
        )
        model.fit(X_train)
        X_score = X[score_idxs]
        preds = model.predict(X_score)
        scores = model.score_samples(X_score)

        means = np.mean(X_train, axis=0)
        stds = np.std(X_train, axis=0)
        stds = np.where(stds == 0, 1.0, stds)
        threshold = float(getattr(model, "offset_", 0.0))

        flagged = [pos for pos, pred in enumerate(preds) if pred == -1]
        if not flagged:
            return []
        deltas = [threshold - scores[pos] for pos in flagged]
        range_min, range_max = min(deltas), max(deltas)
        range_span = range_max - range_min if range_max > range_min else 1.0

        cohort_value_display = cohort_value
        if cohort_field and cohort_value is not None:
            cohort_value_display = cohort_value.split("=", 1)[-1]

        candidates: list[Candidate] = []
        for pos in flagged:
            global_i = score_idxs[pos]
            rec = records[global_i]
            score = scores[pos]
            strength = ((threshold - score) - range_min) / range_span if range_span > 0 else 0.0
            confidence = round(min(0.95, 0.6 + 0.35 * max(0.0, strength)), 2)

            z_scores = (X[global_i] - means) / stds
            contribs = [
                {"feature": name, "value": round(float(X[global_i, j]), 3), "z": round(float(z_scores[j]), 2)}
                for j, name in enumerate(feature_names)
            ]
            contribs.sort(key=lambda c: abs(c["z"]), reverse=True)
            top = contribs[:3]
            top_str = ", ".join(f"{c['feature']} (z={c['z']})" for c in top)
            population = len(train_idxs)
            cohort_phrase = (
                f"{cohort_field}={cohort_value_display} cohort" if cohort_field and cohort_value is not None
                else "similar records"
            )
            why = (
                f"This record is unusual vs. {population} {cohort_phrase}; "
                f"most unusual: {top_str}. Human review required."
            )
            evidence: dict[str, Any] = {
                "method": "Isolation Forest",
                "anomaly_score": round(float(score), 4),
                "n_features": len(feature_names),
                "n_records": population,
                "contamination": self.contamination,
                "random_state": self.random_state,
                "top_contributors": top,
                "why": why,
            }
            if cohort_field and cohort_value is not None:
                evidence["cohort_field"] = cohort_field
                evidence["cohort"] = cohort_value_display

            candidates.append(
                make_candidate(
                    ctx,
                    rec,
                    rule="ml_anomaly",
                    title="Statistically unusual record (ML)",
                    field_name=None,
                    evidence=evidence,
                    confidence=confidence,
                )
            )
        return candidates
