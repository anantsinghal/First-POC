# Salesforce Data Quality Scanner — First POC

A small, local proof of concept for the first trustworthy slice of the Salesforce anomaly-detection blueprint. It connects through the Salesforce REST, Tooling, and Metadata SOAP APIs directly (no Salesforce CLI), or accepts a CSV export for offline demos. It creates an immutable scan manifest and coverage report, profiles the data, produces explainable findings, and lets a reviewer label them.

## What is included

- Manifest-backed, idempotent scan runs with an explicit coverage grade.
- CSV/demo ingestion for `Account`, `Contact`, `Opportunity`, `Case`, or a custom object.
- Deterministic checks: required values, invalid Opportunity stages, missing Opportunity account, Closed Won records without a contract, and non-positive amounts.
- Explainable statistical candidates: numeric IQR outliers and rare field combinations.
- Finding fingerprints and lifecycle state; reviewer feedback persists in SQLite.
- A browser dashboard and JSON API.
- Direct OAuth client-credentials or access-token configuration, REST `describe` + SOQL extraction, Tooling API verification, and Metadata SOAP verification.

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
