# Phase 39 — Per-object data-quality score + trend

## Why
A headline, honest quality metric per scan/object: "% of scanned records with no finding," with a
trend vs. the previous scan of the same object. It aggregates everything (findings + coverage) into one
number stakeholders read at a glance — but it is ALWAYS paired with the coverage grade and a `sampled`
flag so a score from a capped/sampled scan can never be mistaken for org-wide cleanliness.

Generic and reusable: computed from findings + coverage by `record_id`, no object/field hardcoding.

## 1. Repository additions — `app/persistence/repositories.py`
- `FindingRepository.distinct_record_count(conn, scan_id) -> int`:
  `SELECT COUNT(DISTINCT record_id) FROM findings WHERE scan_id=?`.
- `FindingRepository.severity_counts`: add an OPTIONAL `scan_id: str | None = None` keyword. When given,
  add `WHERE scan_id=?`; else global (unchanged default — MetricsService keeps working).
- `ScanRepository.previous_scan_id(conn, object_name, before_created_at) -> str | None`:
  `SELECT id FROM scan_manifests WHERE object_name=? AND created_at<? AND status='completed'
   ORDER BY created_at DESC, rowid DESC LIMIT 1`.

## 2. Service — `app/services/quality_service.py` — `class QualityService`
- `__init__(self, *, database, scans: ScanRepository, coverage_repo: CoverageRepository,
  findings: FindingRepository)`.
- `def for_scan(self, scan_id: str) -> dict[str, Any]`:
  - In one `connect()`: `manifest = scans.get(conn, scan_id)`; if None raise
    `NotFoundError("Scan not found")` (import from `.errors`).
  - `cov = coverage_repo.get(conn, scan_id)`; `observed = cov["observed_records"] if cov else
    manifest.get("row_count", 0)`; `grade = cov["grade"] if cov else None`;
    `sampled = bool(cov and any("record cap" in s for s in cov.get("suppressed_checks", [])))`.
  - `flagged = findings.distinct_record_count(conn, scan_id)`;
    `findings_total = findings.count_for_scan(conn, scan_id)`;
    `by_severity = findings.severity_counts(conn, scan_id=scan_id)`.
  - `clean_record_rate = round(1 - flagged / observed, 4) if observed else None`;
    `quality_score = round(100 * clean_record_rate, 1) if clean_record_rate is not None else None`.
  - Trend: `prev_id = scans.previous_scan_id(conn, manifest["object_name"], manifest["created_at"])`.
    If prev_id: compute its score the same way (observed from its coverage, flagged via
    distinct_record_count) → `previous_quality_score`; `quality_delta = round(quality_score -
    previous_quality_score, 1)` when both are not None, else None. (Factor a small private helper
    `_score(conn, sid) -> float | None` to avoid duplication.)
  - Return dict: `scan_id, object_name, observed_records, flagged_records, findings_total,
    clean_record_rate, quality_score, by_severity, coverage_grade (=grade), sampled,
    previous_scan_id, previous_quality_score, quality_delta`.

## 3. Composition — `app/api/container.py`
Build `quality_service = QualityService(database=database, scans=scans, coverage_repo=coverage_repo,
findings=findings)`; add `quality_service: QualityService` to the `Container` dataclass and the return.

## 4. API — `app/api/routes_scans.py`
Add `GET /api/scans/{scan_id}/quality` -> `try: return container.quality_service.for_scan(scan_id)
except NotFoundError as e: raise HTTPException(404, str(e))`. (Define it AFTER the existing
`/api/scans/{scan_id}` GET; FastAPI matches the more specific `/quality` path fine.)

## 5. Dashboard UI — `app/static/app.js` + `index.html`
- In `refresh()`, after loading the latest scan, if a scan exists fetch
  `/api/scans/${scan.id}/quality` and render into a new `#quality` element: a prominent
  `Quality: <score>%` with the coverage grade, and `(sampled)` when `sampled` is true, and a trend
  arrow using `quality_delta` (▲/▼/—). On no scan, clear it.
- `index.html`: add `<section class="panel"><h2>Data quality</h2><div id="quality"><p>Run a scan to
  populate.</p></div></section>` near the metrics panel. Light CSS reuse (a `.quality-score` big number
  is fine; keep consistent with existing styles).

## 6. Tests — `tests/test_quality.py` (NEW) + extend healthcheck
- Via `tests.harness.api_client`: POST `/api/scans/demo`; GET `/api/scans/{id}/quality`:
  assert `observed_records == 11`, `0 <= quality_score <= 100`, `1 <= flagged_records <= 11`,
  and `clean_record_rate == round(1 - flagged_records/observed_records, 4)`, `previous_scan_id is None`.
- Trend: POST a second `/api/scans/demo`; GET quality of the second scan: `previous_scan_id` is the
  first scan's id and `quality_delta == 0.0` (same data).
- 404: GET `/api/scans/does-not-exist/quality` → 404.
- Extend `tests/test_healthcheck.py`: in the API-smoke block, after the demo scan, assert
  `GET /api/scans/{scan_id}/quality` returns 200 with a numeric `quality_score`.

## Constraints
- `from __future__ import annotations`; type hints; docstrings. No hardcoded object/field names.
- Existing tests stay green; `severity_counts()` default behavior unchanged.

## Done criteria
- `python -m pytest -q` green incl. new quality tests + extended healthcheck. Report files + results and
  confirm the demo scan exposes a quality_score with observed_records=11 and a working trend on re-scan.
