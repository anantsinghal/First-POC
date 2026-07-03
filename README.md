# Salesforce Data Quality Scanner

A coverage-aware, **metadata-driven** Salesforce data anomaly / data-quality detector (FastAPI). It
ingests records (CSV export or direct Salesforce REST/SOQL — no `sf`/`sfdx` CLI), profiles them, grades
how much it could actually inspect (**coverage**), runs a pluggable set of **detectors** that emit
explainable **findings** (candidates for human review), persists an immutable scan manifest + findings
in SQLite, and lets a reviewer label them — which the system then turns into trust metrics and durable
suppression.

Core principle: **missing access is a coverage failure, never proof a value is blank.** Findings are
candidates, not verdicts. Nothing is auto-written back to Salesforce.

> The full long-term product blueprint lives in [`docs/blueprint/`](docs/blueprint/README.md); the
> north-star and current gap map are in [`docs/north-star.md`](docs/north-star.md) and
> [`docs/poc-vs-blueprint.md`](docs/poc-vs-blueprint.md); scale posture in
> [`docs/scalability.md`](docs/scalability.md); the **universal (any-database) platform vision** in
> [`docs/universal-strategy.md`](docs/universal-strategy.md).

## What it does

**Reusable across any org — nothing hardcoded.** Every object/field/picklist/relationship comes from
the org's **metadata** (Salesforce `describe`) or a **data rule catalog**. Supporting a new org or a
new custom object means *ingesting more metadata, not writing code* (proven on standard objects and a
never-before-seen `Project__c`).

**Detectors** (all generic; reconciled so the same issue isn't double-flagged):
- *Schema-driven* (from metadata): required-field-empty, invalid picklist value, missing required
  reference, invalid email/phone/url format.
- *Rule catalog* (business logic as data): e.g. Closed-Won-without-contract, non-positive amount,
  stale-open opportunity, missing close date, stage/account/required checks.
- *Statistical*: IQR outliers, robust MAD (modified z-score) outliers, rare field combinations.
- *Learned*: association-rule pattern breaks; **Isolation-Forest ML** (cohort-aware — scores a record
  against its own segment, not the whole object).
- *Relationship/graph*: orphan / dangling references, missing-expected-children.

The **same engine** also scans SQL databases (SQLite adapter today; same picklist/orphan/required detectors, zero engine change).
- *Duplication*: possible duplicates by Name.

**Trust & operations:**
- Immutable scan manifest; stable finding **fingerprints** + lifecycle (open / reopened / resolved /
  acknowledged / suppressed); idempotent re-scans (set-based bulk upsert).
- **Coverage contract** that never overstates: a record-capped scan is reported `partial`/sampled.
- **Feedback trust loop**: reviewer labels suppress recurring false positives and feed acceptance /
  false-positive **metrics**; per-object **data-quality score + trend**.
- **Org catalog** (persisted schema snapshot), **audit log** (counts only, no record values/secrets),
  **retention/purge** controls.
- A browser dashboard + JSON API.

## Scale & security posture
- **Reusable** across orgs (metadata-driven) and **honest** about scope (sampled scans are labelled).
- **Out-of-core profiling**: large objects are staged to Parquet and profiled with DuckDB SQL (bounded
  memory); a **streaming profile-only scan** (`/api/scans/profile`) never materialises the full record
  set. The scan **planner** tiers work — cheap metadata checks on everything, expensive ML/pattern
  deferred (and reported) on huge objects.
- **Deployment**: designed to run inside the enterprise's own servers; data stays in their boundary;
  raw records are processed transiently (never a standing second copy); secrets never logged/committed.
- See `docs/north-star.md` (§6) and `docs/scalability.md` for the full posture.

## API (selected)
`GET /` · `GET /tutorial` · `GET /api/health`
`POST /api/scans/demo` · `POST /api/scans/upload?object_name=` · `POST /api/scans/multi?primary_object=`
(primary + related CSVs) · `POST /api/scans/profile` (bounded-memory profile only) ·
`POST /api/scans/salesforce` · `POST /api/scans/database?table_name=` · `POST /api/salesforce/verify`
`GET /api/scans` · `GET /api/scans/{id}` · `GET /api/scans/{id}/quality`
`GET /api/findings` · `POST /api/findings/{id}/feedback`
`GET /api/dashboard` · `GET /api/metrics` · `GET /api/audit` · `POST /api/admin/purge`

## Architecture (layered, SOLID; deps point inward)
```
app/
  domain/       Pure value objects + helpers (Candidate, Coverage, Profile, ObjectSchema, enums).
  policy/       FieldPolicy + rule catalog (rules/*.json) — org-specific knowledge as DATA.
  detection/    Detector protocol + registry + reconciliation; rules/ statistical/ schema_driven/
                patterns/ ml/ graph/ detectors (Strategy pattern — add a check, don't touch the engine).
  profiling/    In-memory Profiler + DuckDB ColumnarProfiler (out-of-core, identical output).
  coverage/     CoverageGrader (good|partial|blocked, sampled-aware, data-driven suppressed checks).
  planning/     ScanPlanner (detector tiering + columnar/sampling decisions).
  ingestion/    DataSource protocol + Csv/MultiCsv/Salesforce sources (CsvSource streams iter_batches).
  staging/      Parquet staging store (bounded-memory columnar write).
  salesforce/   Direct REST/Tooling/Metadata-SOAP client; OAuth session (token or client-credentials/ECA).
  persistence/  Database + Repository pattern over SQLite.
  services/     scan / finding / dashboard / metrics / quality / audit / retention orchestration.
  api/          FastAPI routers + DI container (only this layer + main.py import FastAPI).
  main.py       App factory / composition root.
```
Per-phase plans in [`.claude/plans/`](.claude/plans). Tests in `tests/` with a one-command health gate
(`pytest tests/test_healthcheck.py`).

## Run
```powershell
python -m venv .venv ; .\.venv\Scripts\Activate.ps1
pip install -e ".[dev]"          # add ,scale  -> pip install -e ".[dev,scale]"  for duckdb+pyarrow
uvicorn app.main:app --reload    # http://127.0.0.1:8000  (Run demo scan, or upload a CSV)
pytest                            # full suite ; pytest tests/test_healthcheck.py for the quick gate
```

### Connect a Salesforce org (read-only)
Set credentials in your shell (never commit them; `.env` is git-ignored):
- **Production:** `SF_LOGIN_URL` (your My Domain) + `SF_CLIENT_ID` + `SF_CLIENT_SECRET` (Connected App
  or External Client App with client-credentials + a least-privilege Run-As user).
- **Quick test:** `SF_INSTANCE_URL` + `SF_ACCESS_TOKEN` (e.g. from `sf org display`).

Then `POST /api/salesforce/verify` (token-free report of REST/Describe/Tooling/Metadata access) and
`POST /api/scans/salesforce`. See [`docs/salesforce-connection.md`](docs/salesforce-connection.md).

## Status — v1 complete

In scope and done: metadata-driven detection (reconciled, cohort-aware ML, planner-tiered), CSV /
multi-object / live-Salesforce / profile-only scans, coverage contract, review + trust metrics +
quality score, Org catalog, audit log, retention, out-of-core columnar profiling + streaming profile,
dashboard UI. Tested end-to-end (`tests/` + health gate).

Explicitly **deferred to v2** (best built against a live org's real volumes, not synthetic data):
streaming/columnar **detection** at scale, **Bulk API 2.0** extraction, **incremental sync** +
record-state ledger, multi-tenant SaaS internals, and LLM-assisted explanations. Honest limits today:
large online scans are record-capped and reported as *sampled*; the planner defers expensive detectors
on very large objects; no automatic write-back.
