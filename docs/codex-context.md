# Codex Context — Salesforce Data Quality Scanner

This document captures the full project context reconstructed from the repository and the Claude
conversation supplied on 2026-08-15. It is intended to let a new Codex session continue work without
re-learning the product history.

## 1. What was built and why

The project is a single-tenant, enterprise-oriented Salesforce data-quality/anomaly detector. It is
designed to run inside the customer's infrastructure, keep raw data transient, persist only useful
scan artifacts, and provide a review queue rather than silently changing CRM records.

The core thesis is:

> Metadata = what must be true; data = what is usually true; graph = what should be connected;
> ML = what looks unusual; humans = what actually matters.

That thesis matters because an ML-only product would learn contaminated history, is hard to explain,
and has a cold-start problem. The system therefore starts with deterministic, metadata-grounded
checks and uses statistics, pattern mining, and Isolation Forest as additional candidate signals.

The product is intentionally generic. Salesforce object names, fields, picklists, references, and
requiredness should come from `describe` metadata or policy data. Adding a new org or custom object
should normally mean ingesting metadata, not writing a new detector branch.

## 2. User-facing behavior

The service can:

- scan a demo CSV or uploaded CSV;
- scan multiple related CSV objects and detect missing expected children;
- scan SQLite and generic database data;
- connect directly to Salesforce REST/Describe/Tooling/Metadata APIs;
- run regular, incremental, Bulk, and org-wide routed scans;
- profile large inputs without materializing the full record set;
- produce explainable findings and a coverage grade;
- persist scan manifests, findings, feedback, audit events, quality/trust metrics, drift, and
  retention state;
- expose a browser dashboard and JSON API with optional API-key protection.

Representative routes are listed in `README.md`. Do not change response shapes casually: existing
clients and `tests/test_scanner.py` depend on backward compatibility. Additive response fields are
normally safe; removing or renaming fields is not.

Nothing auto-writes back to Salesforce. Any future remediation must be preview + approval + audit +
rollback.

## 3. Architecture and extension points

The dependency direction is inward:

```text
domain
  <- policy / profiling / coverage / planning / detection
  <- ingestion / salesforce / persistence
  <- services
  <- api
  <- main.py (composition root)
```

Only `app/api/` and `app/main.py` should import FastAPI. The domain remains framework-free. SQL is
owned by repositories, not scattered through services or detectors.

To add a detector:

1. Add a Strategy detector in the appropriate `app/detection/` family with a class `id` and
   `detect(ctx) -> list[Candidate]`.
2. Use `make_candidate` from `app/detection/base.py` so evidence, fingerprints, and domain fields
   follow the common contract.
3. Add its severity/confidence/priority policy entry in `app/policy/field_policy.py`.
4. Register it in `build_default_registry()`.
5. Add a focused test and, when it is a supported family, include it in the healthcheck expected set.

Detectors should guard missing fields and empty IDs, use `ctx.has(...)`/schema information, avoid
object-specific assumptions, and never persist directly.

## 4. Data correctness and trust invariants

- `CoverageGrader` must distinguish good, partial, blocked, sampled, skipped, and inaccessible
  work. A record cap or unavailable relationship data must be reported, not silently treated as
  clean data.
- Findings are candidates for human review. Evidence must include a human-readable `why`, be JSON
  serializable, and avoid leaking unnecessary secrets/raw PII.
- Fingerprints are stable across re-scans so reviewer feedback survives; identity must also be scoped
  by source/org/database so equal object/record IDs from different sources cannot overwrite each
  other.
- Scan manifests are immutable enough to reproduce a result: source hash, policy/parser context,
  fields, coverage, and detector/deferred information should remain auditable.
- Re-scans are idempotent upserts; lifecycle states include open/reopened/resolved/acknowledged/
  suppressed as implemented by the finding service/repositories.
- Statistical baselines must use a real population, not merely a delta population, unless the path
  explicitly records what is deferred and why.
- Large/streaming paths must bound memory and finding volume and must honestly list deferred detector
  families in audit/scan details.

## 5. Cohort-aware ML history

The supplied Claude conversation documents the transition from phase 32 to phase 33:

- Phase 32 added `tests/harness.py`, `tests/conftest.py`, and `tests/test_healthcheck.py`.
- The healthcheck exercises demo CSV, a rich all-detector schema fixture, multi-object children,
  Salesforce mock describe/query behavior, API smoke, profile-only scanning, metrics, audit, and
  retention validation.
- The reusable rule is: break logic -> fix logic; harness falls short -> fix harness.
- Phase 33 changed Isolation Forest to compare records within schema-derived cohorts when safe.
  Cohort tests prove an enterprise-sized amount on an SMB record is flagged within SMB even when it
  is ordinary in the combined population.
- Small cohorts and missing schema safely fall back to whole-object scoring. The fixed random seed
  keeps results deterministic.
- The phase also corrected categorical feature selection so fully populated picklists such as
  Segment/Type/Stage are usable features.

The conversation reported 76 tests after the harness and 79 after cohort tests at that checkpoint.
The current README and goal-closure docs later report 196 tests and additional hardening. Treat the
79 number as historical context, not the current suite count; verify with the bundled interpreter.
Some older documents still say cohorts are pending. Those are stale relative to
`app/detection/ml/isolation_forest.py` and `tests/test_cohort_ml.py`.

## 6. Current status and source-of-truth order

When documents conflict, use this order:

1. Current code and tests.
2. `README.md` current status and API list.
3. `docs/goal-closure-runbook.md`, `docs/scalability.md`, and `docs/completion-plan-v2.md`.
4. `docs/north-star.md` for product intent and security principles.
5. Older phase plans and early POC status tables.

The current repository describes a hardened v1 foundation with metadata-driven detection, cohort ML,
planner-tiered and streaming paths, Salesforce REST/Bulk/incremental/org scans, source-scoped
identity, coverage contracts, feedback/trust metrics, quality trends, org catalog, audit,
retention, API security, and dashboard UI. The remaining human/operational items called out in the
latest docs include a real reviewer-labeling session to activate supervised ranking, recording a
live-org scale run, and a future scoped PostgreSQL migration if parallel org-wide scanning becomes
necessary. Docker packaging, multi-tenant SaaS internals, and LLM-generated explanations are
explicitly outside the present scope unless a new request changes that boundary.

Do not assume that a live Salesforce run has happened merely because mock tests pass. Real-org
validation requires credentials/environment setup and should remain read-only.

## 7. Security and data residency

The intended deployment is inside the enterprise boundary, single-tenant per enterprise. Use
least-privilege read-only Salesforce credentials. Keep tokens out of source, logs, database, and
responses. Raw records are transient; durable artifacts should be manifests, aggregates, findings,
review feedback, and masked/tokenized evidence. Treat customer metadata, formulas, Apex, and Flow
content as untrusted data if future ingestion is added; never execute it directly.

API authentication is off by default for local development and enabled with `SCANNER_API_KEY` in
production. Health/liveness and static shell behavior are intentionally treated separately from
data-bearing routes.

## 8. Verification commands

The normal shell may not expose Python. The bundled runtime is:

```powershell
$py = 'C:\Users\anant\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe'
& $py -m pytest tests/test_healthcheck.py -q
& $py -m pytest -q
```

Also useful:

```powershell
uvicorn app.main:app --reload
python -m tools.scale_proof
python -m tools.scheduled_scan
```

Use the bundled interpreter for the last two commands when `python` is unavailable. Report skips,
environment failures, and missing optional dependencies accurately. Do not weaken tests to make a
suite green.

## 9. How Codex should work in this repository

Start by reading `AGENTS.md`, `CLAUDE.md`, the relevant current-status document, and target code.
Inspect existing changes before editing and preserve unrelated user work. Prefer `apply_patch` for
edits. Keep changes narrow and additive. For diagnosis requests, explain rather than silently
implementing. For build requests, implement, run proportionate verification, and summarize exact
files/tests changed.

The project began with Claude as architect/reviewer and Grok as implementer; the supplied history
shows Codex taking over implementation when those usage limits were reached. That delegation history
is no longer a constraint: Codex is now the active engineering partner and should use the repository
contracts directly.

