# Phase 33 — Per-cohort ML scoring (precision) + categorical-feature fix

Implemented directly by Claude (both CLI implementers were credit/usage-limited).

## What changed
- `app/detection/ml/isolation_forest.py` is now **cohort-aware**:
  - Cohort key = a configured `cohort_fields`, else the first schema **picklist** field present with
    2..`max_cohort_cardinality` distinct values. No schema / no suitable picklist → single whole-object
    model (unchanged, backward compatible).
  - Records are grouped by cohort. Cohorts with `>= min_records` get their OWN Isolation Forest (scored
    relative to that cohort). Records in cohorts too small to model fall back to a whole-object model.
    Evidence carries `cohort` / `cohort_field` and `n_records` = the population it was scored against.
  - Refactored into `_build_features`, `_select_cohort_fields`, `_cohort_key`, `_score_subset`.
- **Latent bug fixed**: fully-populated categorical fields (null_rate == 0, e.g. Segment/Type/Stage)
  were previously never one-hot encoded as ML features (a `null_rate in (0,1)` guard). They now are;
  the `1 < distinct <= categorical_max_distinct` check already excludes constant/empty fields. This
  enriches ML for real orgs (categorical context now informs scoring).

## Why (best long-term)
Comparing a record to the WHOLE object hides context-specific anomalies (a $1M deal is normal for
Enterprise, anomalous for SMB) and inflates false positives. Cohort-relative scoring is the
blueprint's per-cohort strategy and directly improves precision — the core trust lever.

## Tests — `tests/test_cohort_ml.py`
- Within-cohort outlier (SMB record with Enterprise-sized Amount) is flagged with `cohort=SMB`,
  `n_records=60` (proves per-cohort scoring, not whole-object).
- Deterministic across runs (fixed random_state).
- No schema → whole-object population (`n_records=120`, no cohort in evidence).

## Verification
`python -m pytest -q` → 79 passed (76 prior + 3). No regression to existing ML / integration /
healthcheck tests; the categorical-feature fix did not destabilize clear-outlier detection.
