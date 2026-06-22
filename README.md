# Salesforce Data Quality Scanner — First POC

A small, local proof of concept for the first trustworthy slice of the Salesforce anomaly-detection blueprint. It connects through the Salesforce REST, Tooling, and Metadata SOAP APIs directly (no Salesforce CLI), or accepts a CSV export for offline demos. It creates an immutable scan manifest and coverage report, profiles the data, produces explainable findings, and lets a reviewer label them.

> **Where this fits:** the full product blueprint lives in [`docs/blueprint/`](docs/blueprint/README.md).
> This repo is its trustworthy-*foundation* slice (rules + statistics + coverage + review). The
> pattern-mining and ML layers are designed but not yet built — see
> [`docs/poc-vs-blueprint.md`](docs/poc-vs-blueprint.md) for the exact gap and the recommended build order.

## What is included

- Manifest-backed, idempotent scan runs with an explicit coverage grade.
- CSV/demo ingestion for `Account`, `Contact`, `Opportunity`, `Case`, or a custom object.
- Deterministic checks: required values, invalid Opportunity stages, missing Opportunity account, Closed Won records without a contract, and non-positive amounts.
- Explainable statistical candidates: numeric IQR outliers, robust MAD (modified z-score) outliers, and rare field combinations.
- Cross-field, temporal, and duplication checks: open Opportunities past their Close Date, possible duplicates by Name, and missing Close Dates.
- Finding fingerprints and lifecycle state; reviewer feedback persists in SQLite.
- A browser dashboard and JSON API.
- Direct OAuth client-credentials or access-token configuration, REST `describe` + SOQL extraction, Tooling API verification, and Metadata SOAP verification.

## Architecture

The code is a layered, SOLID design where dependencies point inward and each concern is independently testable. New anomaly checks are added by writing a `Detector` strategy and registering it — the engine, services, and API do not change.

```
app/
  domain/       Pure value objects + helpers (Candidate, Coverage, Profile, enums). No I/O.
  policy/       FieldPolicy — expected fields, valid stages, severity/score table (configuration).
  detection/    Detector protocol + registry (Strategy pattern); rules/ and statistical/ detectors.
  profiling/    Profiler — per-field null-rate / distinct / top-values / numeric summary.
  coverage/     CoverageGrader — good | partial | blocked, with suppressed checks (policy-driven).
  ingestion/    DataSource protocol + CsvSource and SalesforceObjectSource implementations.
  salesforce/   Direct REST/Tooling/Metadata-SOAP client (no sf/sfdx CLI).
  persistence/  Database + Repository pattern over SQLite (no SQL leaks into services).
  services/     ScanService / FindingService / DashboardService orchestration (framework-free).
  api/          FastAPI routers + DTOs + a DI container on app.state.
  main.py       Composition root / app factory (the only place concretes are wired together).
```

Implementation plans for each layer live in [`.claude/plans/`](.claude/plans). Only the `api/` layer
and `main.py` import FastAPI; everything below is framework-free and unit-tested in isolation.

## Deliberate boundaries

The POC calls Salesforce directly, but intentionally bounds an online scan to 2,000 records and a small allowlist of fields. A production connection needs a sandbox Connected App, least-privilege OAuth credentials, field-policy approval, and Bulk API 2.0 extraction with checkpointing. The [`docs/salesforce-connection.md`](docs/salesforce-connection.md) guide captures that hand-off. We do not claim an org-wide or runtime-complete scan.

## Run locally

```powershell
cd "C:\Users\anant\OneDrive\Documents\Grok POC\First POC"
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e ".[dev]"
uvicorn app.main:app --reload
```

Open <http://127.0.0.1:8000>. Select **Run demo scan** to use the seeded Opportunity sample, or upload an export. Then run tests with:

```powershell
pytest
```

## Connect Salesforce without CLI

Copy `.env.example` to your local environment and configure either `SF_INSTANCE_URL` + `SF_ACCESS_TOKEN`, or a Connected App's `SF_LOGIN_URL`, `SF_CLIENT_ID`, and `SF_CLIENT_SECRET`. Start the server from that environment, then call:

```powershell
Invoke-RestMethod -Method Post http://127.0.0.1:8000/api/salesforce/verify
Invoke-RestMethod -Method Post http://127.0.0.1:8000/api/scans/salesforce -ContentType application/json -Body '{"object_name":"Opportunity","max_records":500}'
```

`/api/salesforce/verify` makes separate direct requests to REST/Describe, Tooling (`ApexClass` query), and Metadata SOAP (`listMetadata` for `CustomObject`), reporting partial permission failures without exposing any token.

## CSV requirements

An `Id` column is required. For Opportunity checks, use `Name`, `StageName`, `Amount`, `AccountId`, and `Contract__c` where available. Field absence lowers coverage and suppresses checks that would otherwise be misleading.
