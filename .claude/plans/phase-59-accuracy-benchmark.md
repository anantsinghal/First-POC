# Phase 59 — Accuracy benchmark with ground truth ("accurately" becomes a number)

## Why
The product's goal says it finds anomalies **accurately** — but no precision/recall number exists
anywhere. This phase builds a deterministic benchmark: generate datasets where every planted anomaly
is KNOWN, scan them with the real engine, score findings against ground truth, and emit a report.
Contract-family accuracy becomes a CI-enforced invariant; statistical/ML accuracy becomes a tracked
metric with honest caveats.

HARD CONSTRAINTS: zero changes under `app/` AT ALL — this phase is tools/ + tests/ + docs only (the
benchmark exercises the engine as a black box through the container; if the benchmark reveals an
engine bug, STOP and report it as BLOCKED — do not fix engine code in this phase). No new deps
(sqlite3/csv/random stdlib; sklearn/duckdb optional via existing extras). Deterministic seed
everywhere (`random.Random(1234)`).

## 1. Generator — `tools/benchmark.py` (single file, also runnable: `python -m tools.benchmark`)
(Create `tools/__init__.py` empty if needed for -m execution.)

`def generate_dataset(dir: Path, *, rows: int = 2000, seed: int = 1234) -> dict`:
Builds ONE SQLite db `bench.db` with:
- `owners(id INTEGER PRIMARY KEY, name TEXT NOT NULL)` — 20 clean owners.
- `deals(id INTEGER PRIMARY KEY, owner_id INTEGER NOT NULL REFERENCES owners(id),
  stage TEXT NOT NULL CHECK (stage IN ('open','won','lost')), amount REAL, email TEXT, website TEXT)`
- `rows` clean records: owner_id in 1..20, valid stage mix, amount ~ N(50_000, 2_000) via
  `rnd.gauss` clamped positive, email `user{i}@ex.com`, website `https://ex{i}.com`.
- PLANTED anomalies (seeded positions spread across the table, ~1% of rows, each recorded in a
  ground-truth list as `{"record_id", "family", "field"}`):
  - `picklist_invalid`: stage = 'zombie' (via PRAGMA ignore_check_constraints=ON) — k=5
  - `required_missing`: stage = '' — k=5
  - `orphan_reference`: owner_id = 9999 — k=5
  - `invalid_format`: email = 'not-an-email' (k=3) and website = 'ht!tp:/broken necklace' (k=2)
    (NOTE: website invalids must contain whitespace or lack any domain-dot per current validator —
    schemeless domains and /paths are VALID by design; pick values that are unambiguously broken)
  - `numeric_outlier`+`robust_outlier` (one family "stat_outlier" in ground truth): amount =
    5_000_000 (k=3) and amount = 0.01 (k=2)
- Return `{"db_path", "table": "deals", "truth": [...], "rows_total"}` and also write
  `truth.json` beside the db.

## 2. Scorer — same file
`def score(findings: list[dict], truth: list[dict]) -> dict`:
- Family mapping: a truth item matches a finding when finding.record_id == truth.record_id AND
  finding_type ∈ FAMILY_MAP[truth.family] where FAMILY_MAP = {
  "picklist_invalid": {"picklist_invalid"}, "required_missing": {"required_missing"},
  "orphan_reference": {"orphan_reference"}, "invalid_format": {"invalid_format"},
  "stat_outlier": {"numeric_outlier", "robust_outlier"} }.
- Per family: `recall = matched_truth / total_truth`.
- Precision, computed honestly in two buckets:
  - CONTRACT families (picklist/required/orphan/format): `precision = true_positive_findings /
    all_findings_of_those_types` — every such finding must correspond to a planted anomaly (the
    clean rows are constructed to be contract-clean), so expected precision == 1.0.
  - STATISTICAL bucket: report `flagged_not_planted` count for numeric_outlier/robust_outlier/
    ml_anomaly separately as "candidates beyond ground truth" — NOT called false positives (Gaussian
    data legitimately has tail values; ML contamination flags ~2% by design). The report must say
    this explicitly.
- Return `{"per_family": {family: {"recall", "precision"|None, "matched", "planted"}},
  "extra_statistical_candidates": n, "findings_total": n}`.

## 3. Runner — same file
`async def run_benchmark(workdir: Path, *, rows=2000, seed=1234) -> dict`:
- generate → `build_container(data_dir=workdir/"scanner", root=<repo root>)` → scan `deals` BOTH ways:
  (a) `run_scan(SqliteTableSource(...))` (in-memory path) and (b) `stream_scan(SqliteTableSource(...))`
  (streaming path) into two separate containers/data dirs. Score EACH against the same truth.
- Emit `benchmark-results.md` in workdir: a table per path (family | planted | matched | recall |
  precision), the extra-statistical-candidates note, rows/seed/date, and engine test-count context.
- `main()` (argparse: --rows, --seed, --out) prints the summary table to stdout and exits 0.

## 4. CI test — `tests/test_benchmark.py` (NEW)
`test_benchmark_contract_accuracy(tmp_path)` (importorskip duckdb/pyarrow only for the stream half;
run the in-memory half unconditionally):
- `run_benchmark(tmp_path, rows=800, seed=1234)` (small = fast).
- ASSERT for the in-memory path: every CONTRACT family has recall == 1.0 AND precision == 1.0;
  stat_outlier recall >= 0.8 (planted 5M/0.01 extremes are unmissable; threshold gives slack).
- ASSERT for the streaming path (when scale deps present): contract families recall == 1.0.
- Keep total test runtime < ~30s.

## 5. Docs
- Run `python -m tools.benchmark --rows 2000` once at the repo root with `--out docs/` so
  `docs/benchmark-results.md` is checked in as the canonical current-accuracy report.
- README: under Status add one line linking to docs/benchmark-results.md ("accuracy is measured, not
  asserted").

## Done criteria
Full suite + healthcheck green (nothing under app/ changed — verify with git diff). Report: files
changed, test tail, and CONFIRM: (a) contract-family precision/recall from the checked-in report
(both paths); (b) stat recall; (c) extra-statistical-candidates count and that the report explains
it; (d) full-suite count. If ANY contract family scores < 1.0, report BLOCKED with the mismatch
details instead of loosening the assertion — that would be a real engine bug for the architect.
