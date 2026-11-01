# Codex Working Context

This file is the repository-level operating context for Codex. Read it together with
`CLAUDE.md`, `README.md`, and `docs/codex-context.md`. The detailed product intent lives in
`docs/north-star.md`; the implementation/status map lives in `README.md` and
`docs/poc-vs-blueprint.md`.

## Product in one sentence

This is a coverage-aware, metadata-driven Salesforce data-quality and anomaly scanner. It reads
an org's schema and data, emits explainable candidates for human review, persists scan history and
feedback, and never writes data back to Salesforce automatically.

## Non-negotiable principles

- Metadata says what must be true; observed data says what is usual; relationships say what should
  be connected; ML says what looks unusual; humans decide what matters.
- Missing access is a coverage failure, never evidence that a value is blank.
- Findings are candidates, not verdicts. No automatic Salesforce write-back.
- Org-specific knowledge belongs in metadata/catalogs/policy data, not object-specific Python logic.
- Every finding must be explainable, serializable, and traceable to a stable fingerprint.
- Preserve source/org isolation. A finding, sync state, ledger, drift baseline, and feedback must not
  collide across data sources.
- Prefer additive Strategy-based detector changes. Avoid changing the engine, service, persistence,
  or API shape for a new detector unless the task explicitly requires it.

## Current implementation

The application is a FastAPI service with a layered architecture:

`domain` <- `policy` / `profiling` / `coverage` / `detection` <- `ingestion` / `salesforce` /
`persistence` <- `services` <- `api` <- `main.py`.

Important areas:

- `app/domain/`: framework-free records, candidates, schemas, coverage, enums, and values.
- `app/policy/`: generic field policy and JSON rule catalog; business knowledge as data.
- `app/detection/`: registry/reconciliation plus schema-driven, rule, statistical, pattern, ML, and
  graph detectors.
- `app/profiling/` and `app/staging/`: in-memory and bounded-memory columnar profiling.
- `app/ingestion/`: CSV, multi-object CSV, SQLite, Salesforce REST/SOQL, Bulk, and incremental
  sources.
- `app/salesforce/`: direct REST/Describe/Tooling/Metadata client and OAuth/session handling; no
  `sf`/`sfdx` CLI dependency in the application.
- `app/persistence/`: SQLite database and repositories; production PostgreSQL is a planned
  migration, not the current default.
- `app/services/`: scan, findings, quality, dashboard, metrics, audit, retention, sync, ranking,
  learning, and org orchestration.
- `app/api/`: FastAPI routers, schemas, security, and dependency container.
- `app/static/`: browser dashboard and ML tutorial.
- `tests/harness.py`: reusable deterministic builders/assertions and `tests/test_healthcheck.py`:
  the one-command system health gate.

Detector families currently represented include required-field, picklist, format, reference and
orphan checks; rule-catalog checks; IQR/MAD/rare-combination statistics; association pattern breaks;
Isolation Forest ML; missing children; and duplicate-name checks. Findings carry severity,
confidence, priority, evidence, and a stable fingerprint.

## ML decision and current cohort behavior

ML is deliberately one signal, not the product's source of truth. Pure ML is unsafe here because
historical data can contain the very errors being audited, opaque scores reduce enterprise trust,
and new/rare objects have too little data. Metadata rules work on day one and provide the reason a
reviewer can act on; ML adds unknown-unknown detection.

`app/detection/ml/isolation_forest.py` is cohort-aware. When schema metadata exposes a usable
low-cardinality picklist (or an explicitly configured cohort field), records are scored against
their own cohort. Large enough cohorts get their own model; tiny cohorts fall back to a whole-object
baseline; with no schema/cohort the behavior remains whole-object. The model is deterministic and
its evidence includes cohort, population size, and contributing features when applicable.

The cohort work also fixed a latent feature bug: fully populated low-cardinality categorical fields
were previously skipped because of a null-rate guard, so fields such as segment/type/stage did not
inform ML. They now participate when they have more than one distinct value.

## Verification contract

Use the bundled Python runtime if the normal shell has no Python:

`C:\Users\anant\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe -m pytest -q`

Fast gate:

`...\python.exe -m pytest tests/test_healthcheck.py -q`

The harness contract is: when logic breaks, fix logic; when the harness cannot express a valid
case, extend the harness. A new detector should normally add a focused test and update the health
gate's expected family set. Keep `tests/test_scanner.py` green because it is the API compatibility
contract.

## Safe implementation workflow

1. Read this file, `CLAUDE.md`, the relevant blueprint/current-status docs, and the target modules.
2. Preserve existing user changes and inspect before editing.
3. For a new anomaly check: implement a detector with `id` and `detect(ctx)`, use
   `make_candidate`, add policy severity/confidence/priority, register it, and add tests.
4. Do not hardcode Salesforce object/field names when metadata or policy can express the rule.
5. Run the focused tests, then the health gate, then the full suite when practical.
6. Report actual test results; never claim a suite passed if the environment prevented execution.

## Current strategic status

The codebase is beyond the original small Opportunity POC: it has CSV, multi-object, SQLite,
Salesforce REST/Bulk/incremental/org-wide paths, coverage honesty, immutable manifests, finding
lifecycle and reviewer feedback, quality/trust metrics, audit/retention, API-key security,
columnar/streaming paths, planner tiering, pattern mining, graph checks, and cohort-aware ML.

The remaining product-level work is mostly operational/enterprise hardening and validation rather
than adding a second opaque AI layer. Read `README.md`, `docs/goal-closure-runbook.md`, and
`docs/scalability.md` before declaring a gap done. Older docs such as early phase plans can describe
pre-cohort or pre-hardening states; prefer code and the latest status docs when they disagree.

