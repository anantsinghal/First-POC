# Phase 54 — Streaming statistical + sampled-ML detection (two-pass over staged Parquet)

## Why
Today `stream_scan` runs ONLY tier-0 checks; statistical (IQR/MAD) and ML detectors are switched off
on big objects — so the largest objects get the least intelligence. Fix: a **two-pass design** using
the Parquet file we already stage. Pass 1 (existing): stream batches → tier-0 detect → checkpoint →
tee to Parquet (+ collect a small reservoir sample). Pass 2 (new, before the Parquet is purged):
DuckDB computes exact aggregates out-of-core; then we re-read the Parquet in batches and flag
records against those aggregates — bounded memory throughout. Sampled Isolation Forest trains on the
reservoir and scores every batch.

DEPENDS ON Phase 53 (BatchParquetWriter + per-batch checkpointing) being merged.

HARD CONSTRAINTS: engine changes limited to the three refactors listed below (pure extraction, no
behavior change to existing detectors — their tests must pass unmodified). No source-system coupling
in detection. No new dependencies. All existing tests stay green.

## 1. Shared math extraction (so streamed + in-memory can NEVER drift)
`app/detection/statistical/thresholds.py` (NEW, pure functions + constants):
- `IQR_MULTIPLIER = 1.5`; `def iqr_fences(q1: float, q3: float) -> tuple[float, float] | None`
  (None when iqr <= 0).
- `MAD_SCALE = 0.6745`; `MAD_THRESHOLD = 3.5`; `def mad_z(value: float, med: float, mad: float) ->
  float` (mad<=0 guard handled by caller).
REFACTOR `iqr_outlier.py` and `mad_outlier.py` to import and use these (identical behavior —
existing tests unchanged and must pass).

## 2. Feature-spec extraction for sampled ML
`app/detection/ml/isolation_forest.py` — extract the feature engineering into module functions:
- `build_feature_spec(records, fields, *, categorical_max_distinct=8) -> FeatureSpec` where
  `FeatureSpec` is a small frozen dataclass capturing, per output feature: kind
  (numeric|date|presence|onehot), source field, medians for imputation, one-hot value. Built exactly
  like today's `_build_features` (same rules/rates).
- `def vectorize(records, spec) -> "np.ndarray"` — applies a spec to ANY record batch (values/
  categories unseen in the spec → imputed/zeros).
- REFACTOR the existing `IsolationForestDetector._build_features` to call these two (identical
  matrices — existing ML tests unchanged and must pass).
- NEW `class StreamingIsolationForest` in the same module: `__init__(*, contamination=0.02,
  random_state=42, n_estimators=200, min_sample=50)`; `def fit(self, sample_records, fields) ->
  bool` (builds spec from the sample, fits the forest on the sample; False when sklearn missing or
  sample too small/thin); `def score_batch(self, records) -> list[tuple[int, float, list[dict]]]`
  returning (index-in-batch, anomaly_score, top_contributors-vs-SAMPLE-stats) for records predicted
  -1 by the sample-fitted model. Deterministic given the seed.

## 3. The pass-2 service — `app/services/streaming_stats.py` — `class StreamingStatsPass`
Data-plane code (may import duckdb/pyarrow guarded, and the pure threshold/vectorize functions).
- `def numeric_aggregates(self, parquet_path, fields) -> dict[str, dict]`: one DuckDB connection;
  for each field, `TRY_CAST(REPLACE("f", ',', '') AS DOUBLE)`: count n; skip if n < 8; compute
  `quantile_cont(0.25/0.5/0.75)`; second query per qualifying field for
  `median(abs(x - med))` (MAD). Return `{field: {"n", "q1", "med", "q3", "mad"}}`.
  HONESTY NOTE (docstring + evidence): streamed IQR uses continuous quantiles; the in-memory
  detector uses index-based quartiles — values can differ slightly at small n; at streaming scale the
  difference is negligible. Evidence carries `"method": "IQR (streamed)"` / `"MAD modified z-score
  (streamed)"`.
- `def flag_batches(self, parquet_path, fields, aggregates, *, object_name, policy, batch_size=5000)
  -> Iterator[list[Candidate]]`: iterate the Parquet with `pyarrow.parquet.ParquetFile.iter_batches`;
  for each batch build the record dicts, one lightweight `DetectionContext(object_name, batch,
  fields, policy)`, and for each record/numeric field: fences from `iqr_fences(q1,q3)` → candidate
  `numeric_outlier` (evidence: observed_value, lower/upper bounds, method, why) and
  `mad_z(...) > MAD_THRESHOLD` → candidate `robust_outlier` (evidence: median, mad, modified_z,
  method, why) — SAME finding_types as in-memory so fingerprints stay stable. Yield candidates per
  batch.
- `def score_ml_batches(self, parquet_path, fields, forest: StreamingIsolationForest, *, object_name,
  policy, batch_size=5000) -> Iterator[list[Candidate]]`: same batch iteration; use
  `forest.score_batch(records)` → `ml_anomaly` candidates (confidence mapping like the in-memory
  detector: 0.6..0.95 by normalized strength within the batch's flagged set; evidence:
  `"method": "Isolation Forest (sampled, streamed)"`, anomaly_score, sample_size, top_contributors,
  why mentioning it was scored against a sample-trained model).

## 4. Wire into `stream_scan` (app/services/scan_service.py)
- Pass 1 additions: maintain a deterministic reservoir sample (`random.Random(42)`, classic
  reservoir-k with k=5000) of records across all batches (cheap; sample only).
- After `writer.close()` and the columnar profile, BEFORE deleting the Parquet, when
  `self._columnar_available()`:
  - `aggs = StreamingStatsPass().numeric_aggregates(path, fields)`; if any: for each candidate batch
    from `flag_batches(...)`: `upsert_many + conn.commit()` (checkpointed like tier-0); count into
    `total_findings`/`flagged`.
  - ML: `forest = StreamingIsolationForest(); if forest.fit(sample, fields):` same loop over
    `score_ml_batches(...)`.
- Notes/audit update: `"streamed scan: N findings (tier-0 + streamed stats{ + sampled ML}) over B
  batches; pattern mining and graph checks deferred (streaming)"`; audit detail `deferred` now lists
  only the genuinely deferred ids (`pattern_break`, `rare_combination`, `duplicate_name`,
  `missing_children`, and `ml_anomaly` ONLY when the fit was skipped).
- When scale deps unavailable: unchanged current behavior (tier-0 only + note).

## 5. Tests — `tests/test_streaming_stats.py` (NEW; importorskip duckdb, pyarrow, sklearn where used)
1. **Extraction parity**: run the demo through `run_scan` before/after — existing suite covers this;
   plus direct: `iqr_fences`/`mad_z` give identical fences/z to hand-computed values.
2. **Streamed stats catch planted outliers**: SQLite table, 4000 rows Amount~100000±500, plant id
   'X1' Amount=9_000_000 and 'X2' Amount=5; `stream_scan` with batch_size=500 → findings include
   `numeric_outlier` AND `robust_outlier` whose record ids ⊇ {X1}; evidence method contains
   "streamed".
3. **Equivalence with in-memory on the same data**: the same 4000 rows via `run_scan` (fits in
   memory) — assert the set of record ids flagged `robust_outlier` by streaming equals the in-memory
   set (MAD is deterministic; IQR may differ at the margin — assert X1 in both, don't require exact
   set equality for IQR).
4. **Sampled ML**: plant a multivariate oddball; assert an `ml_anomaly` finding exists with method
   containing "sampled"; run twice → identical flagged ml ids (determinism).
5. **Fallback regression**: monkeypatch `_columnar_available` → False; stream_scan still completes,
   tier-0 only, notes mention deferral (existing behavior preserved).

## 6. Docs
`docs/scalability.md`: roadmap item 5 (online/2-pass detectors) → done for stats + sampled ML;
pattern mining at scale remains deferred (honest note).

## Done criteria
Full suite + healthcheck green (existing statistical/ML tests UNMODIFIED and passing — proves the
refactors changed nothing). Report files changed, tails, and confirmations: planted outlier caught by
streamed stats; streamed-vs-in-memory MAD id sets equal; sampled ML deterministic.
