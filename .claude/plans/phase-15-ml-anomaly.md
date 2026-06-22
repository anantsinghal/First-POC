# Phase 15 — ML anomaly scorer (Isolation Forest)

Blueprint Epic 7 + finding type `ml_anomaly` (`docs/blueprint/10-data-models.md`,
`docs/blueprint/05-ml-anomaly-detection.md`). The first real trained model: an unsupervised Isolation
Forest over engineered per-object features, emitting record-level `ml_anomaly` candidates with
explainable evidence. Pure additive Strategy. Guardrails from
`docs/blueprint/15-architecture-review-and-hardening-plan.md`: skip sparse cohorts, fixed seed for
reproducibility (replayability), candidates not verdicts, explainable (no opaque scores).

## 1. Dependencies — `pyproject.toml`
Add an OPTIONAL extra (do NOT add to core `dependencies`, keep the base app light):
```
[project.optional-dependencies]
ml = ["scikit-learn>=1.3", "numpy>=1.24"]
```
(Keep the existing `dev` extra.)

## 2. Policy — `app/policy/field_policy.py`
Add `"ml_anomaly": (Severity.MEDIUM, 0.70, 0.65)` (default; detector overrides confidence per finding).

## 3. Detector — `app/detection/ml/__init__.py` (empty) and
`app/detection/ml/isolation_forest.py` — `class IsolationForestDetector`
- **Guarded import** so the app runs even without scikit-learn installed:
  ```
  try:
      import numpy as np
      from sklearn.ensemble import IsolationForest
      _SKLEARN_AVAILABLE = True
  except Exception:
      _SKLEARN_AVAILABLE = False
  ```
- `id: str = "ml_anomaly"`; `requirement: ClassVar[CheckRequirement | None] = None`.
- `__init__(self, *, min_records: int = 50, contamination: float = 0.02, random_state: int = 42,
  categorical_max_distinct: int = 8, n_estimators: int = 200)`.
- `detect(ctx)`:
  1. If `not _SKLEARN_AVAILABLE`: return `[]`.
  2. `records = [r for r in ctx.records if clean(r.get("Id"))]`; if `len(records) < min_records`
     return `[]` (sparse-cohort guardrail).
  3. **Feature engineering** (build a numeric matrix + parallel `feature_names`):
     - Numeric fields: a field is numeric if `parse_float` succeeds for ≥ 60% of non-empty values.
       Column = `parse_float(value)`, impute missing with the column median. feature name = field.
     - Date fields: a field is a date if `parse_date` succeeds for ≥ 60% of non-empty values.
       Column = `parse_date(value).toordinal()`, impute median. feature name = `f"{field}__ordinal"`.
     - Presence booleans: for fields with `0 < null_rate < 1` (and not already numeric/date), column =
       1.0 if present else 0.0. feature name = `f"has_{field}"`.
     - Low-cardinality categoricals: for non-numeric/non-date fields (excluding Id, Name) with
       `1 < distinct ≤ categorical_max_distinct`, one-hot each value: column = 1.0/0.0. feature name =
       `f"{field}={value}"`.
     - If fewer than 2 feature columns result, return `[]` (not enough signal).
  4. Build `X = np.array(rows, dtype=float)`. Fit
     `IsolationForest(n_estimators=n_estimators, contamination=contamination,
     random_state=random_state).fit(X)`. Compute `preds = model.predict(X)` (-1 = outlier) and
     `scores = model.score_samples(X)` (lower = more anomalous).
  5. **Explainability**: precompute per-feature mean and std over X. For each flagged record (pred==-1),
     compute per-feature z = `(value - mean)/std` (std==0 → 0); pick the top 3 features by `abs(z)` as
     `top_contributors` = list of `{"feature": name, "value": round(v,3), "z": round(z,2)}`.
  6. **Per-finding confidence**: map outlierness to a bounded confidence. Let `s = scores[i]`; over the
     flagged set, normalise `strength = (threshold - s)` against the flagged range to [0,1]
     (guard divide-by-zero); `confidence = round(min(0.95, 0.6 + 0.35 * strength), 2)`.
  7. For each flagged record emit
     `make_candidate(ctx, rec, rule="ml_anomaly", title="Statistically unusual record (ML)",
     field_name=None, evidence={...}, confidence=<computed>)` where evidence =
     `{"method": "Isolation Forest", "anomaly_score": round(float(s), 4), "n_features": <k>,
     "n_records": len(records), "contamination": contamination, "random_state": random_state,
     "top_contributors": [...], "why": "This record is unusual vs. <n_records> similar records; most
     unusual: <feat> (z=<z>), ... Human review required."}`.
  - Keep field_name = None (record-level) so the fingerprint stays stable across runs even if the top
    contributor changes.

## 4. Registry — `app/detection/registry.py`
APPEND `IsolationForestDetector()` to `build_default_registry()`.

## 5. Tests — `tests/test_ml_anomaly.py` (NEW)
(Use a module-level `pytest.importorskip("sklearn")` so the suite is clean on machines without it.)
- Build ~120 synthetic records: ~115 "normal" (Amount clustered ~100000±5000, consistent categorical
  values, fields present) + 5 clear outliers (Amount ~5_000_000 and/or odd combinations). Assert
  `IsolationForestDetector(contamination=0.05).detect(ctx)` returns ≥ 1 finding, all
  `finding_type=="ml_anomaly"`, and that the 5 injected outlier Ids are a subset (or majority subset)
  of the flagged set; each finding's evidence has `anomaly_score`, `top_contributors` (non-empty), and
  `confidence` within [0.55, 0.96].
- **Reproducibility**: two `detect` runs on the same context yield the identical set of flagged
  record_ids and identical `anomaly_score`s (fixed `random_state`).
- Tiny dataset (< `min_records`) → `[]`.
- Demo regression: the 11-row `data/demo_opportunities.csv` via `build_default_registry().detect(...)`
  still yields the same count as before (ml_anomaly fires 0 because 11 < min_records); assert no
  `ml_anomaly` on the demo.

## Constraints
- scikit-learn/numpy used ONLY inside `app/detection/ml/` behind the guarded import. The app and all
  other tests must import and run even if sklearn is absent.
- `from __future__ import annotations`; type hints; docstrings. Do NOT modify `tests/test_scanner.py`,
  `tests/test_detection.py`, `tests/test_new_detectors.py`, `tests/test_pattern_miner.py`, or any
  service/persistence/api file.

## Done criteria
- `python -m pytest -q` green; 11-row demo finding count unchanged (no ml_anomaly). Report files
  created/changed, the demo count, and how many ml_anomaly findings the large fixture produces.
