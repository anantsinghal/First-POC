# Phase 5 — Ingestion sources + Salesforce relocation

Two jobs: (A) relocate the monolithic `app/salesforce.py` into a small package WITHOUT changing
behavior or its public import surface, and (B) introduce a `DataSource` abstraction with CSV and
Salesforce implementations so the scan service depends on an interface, not a concrete loader.

Do NOT modify `app/main.py` or `tests/test_scanner.py`. `app/main.py` does
`from app.salesforce import SAFE_API_NAME, SalesforceClient, SalesforceError,
session_from_environment, verify_direct_api_access` and `tests/test_scanner.py` does
`from app.salesforce import SalesforceClient, SalesforceSession` — these MUST keep working via
package re-exports.

## A. Relocate `app/salesforce.py` -> `app/salesforce/` package
Move the code VERBATIM (no logic edits), split by responsibility:
- `app/salesforce/session.py`: `SalesforceError`, `SalesforceSession`, `session_from_environment`.
- `app/salesforce/client.py`: `SAFE_API_NAME`, `SalesforceClient` (import `SalesforceError`,
  `SalesforceSession` from `.session`).
- `app/salesforce/verify.py`: `verify_direct_api_access` (import from `.session` and `.client`).
- `app/salesforce/__init__.py`: re-export ALL of: `SAFE_API_NAME`, `SalesforceError`,
  `SalesforceSession`, `SalesforceClient`, `session_from_environment`, `verify_direct_api_access`
  (and set `__all__`).
- DELETE the old `app/salesforce.py`.
Keep `tests/test_scanner.py`'s Salesforce MockTransport test passing unchanged.

## B. Ingestion abstraction — `app/ingestion/`
`app/ingestion/__init__.py` — empty package marker.

`app/ingestion/base.py`:
- `@dataclass(frozen=True) class LoadedDataset`: `object_name: str`, `source_name: str`,
  `fields: list[str]`, `records: list[Record]`, `source_hash: str`.
- `class IngestionError(RuntimeError)`: `__init__(self, message: str, status_code: int = 422)` storing
  `self.status_code`. (Used for "cannot query Id" -> HTTP 422 later.)
- `@runtime_checkable class DataSource(Protocol)`: `async def load(self) -> LoadedDataset: ...`
  (async so CSV and Salesforce share one interface; CSV just does sync work in an async method).

`app/ingestion/csv_source.py` — `class CsvSource(DataSource)`:
- `__init__(self, *, raw_bytes: bytes, object_name: str, source_name: str)`.
- `async def load(self)`: decode `raw_bytes.decode("utf-8-sig")`, `csv.DictReader`, `fields =
  reader.fieldnames or []`, `records = [{k: clean(v) for k,v in row.items() if k is not None} for row
  in reader]` (same as baseline persist_scan). On `UnicodeDecodeError`/`csv.Error` raise
  `IngestionError(f"Could not parse CSV: {exc}", status_code=400)`. `source_hash =
  hashlib.sha256(raw_bytes).hexdigest()`. Return `LoadedDataset(object_name, source_name, fields,
  records, source_hash)`.

`app/ingestion/salesforce_source.py` — `class SalesforceObjectSource(DataSource)`:
- Module constant `DEFAULT_SF_POLICY_FIELDS = ["Id","Name","StageName","Amount","AccountId",
  "Contract__c","Segment__c","CloseDate"]` (same list baseline used in `scan_salesforce`).
- `__init__(self, *, client: SalesforceClient, session: SalesforceSession, object_name: str,
  max_records: int = 500, policy_fields: list[str] = DEFAULT_SF_POLICY_FIELDS)`.
- `async def load(self)`:
  - `describe = await client.describe_sobject(object_name)`
  - `readable = {f.get("name") for f in describe.get("fields", []) if f.get("name") and
    f.get("queryable")}`
  - `fields = [f for f in policy_fields if f in readable]`
  - if `"Id" not in fields`: raise `IngestionError("The integration user cannot query Id for this
    object; no trustworthy scan is possible.", status_code=422)`
  - `records = await client.query_records(f"SELECT {', '.join(fields)} FROM {object_name}",
    max_records=max_records)`
  - Build cleaned records limited to `fields`: `cleaned = [{f: clean(str(r[f])) if r.get(f) is not
    None else "" for f in fields} for r in records]` — i.e. each field present, value cleaned to a
    string, missing/None -> "". (Mirrors baseline's CSV round-trip which dropped `attributes` and
    coerced to strings.)
  - `host = session.instance_url.split("//", 1)[-1]`; `source_name = f"salesforce:{host}/{object_name}"`.
  - `source_hash = stable_hash({"fields": fields, "records": cleaned})` (deterministic per content).
  - Return `LoadedDataset(object_name, source_name, fields, cleaned, source_hash)`.

## Tests — `tests/test_ingestion.py` (NEW)
- Relocation smoke: `from app.salesforce import SAFE_API_NAME, SalesforceError, SalesforceSession,
  SalesforceClient, session_from_environment, verify_direct_api_access` all import.
- `CsvSource`: load `data/demo_opportunities.csv` bytes -> dataset.object_name=="Opportunity",
  `fields` == the 8 CSV headers, `len(records)==11`, `source_hash == sha256(bytes)`.
- `CsvSource` bad bytes (`b"\xff\xfe\x00bad"` or a malformed case) -> raises `IngestionError`.
- `SalesforceObjectSource` with a `SalesforceClient` backed by `httpx.MockTransport` that answers
  `/sobjects/Opportunity/describe` with queryable Id+Name+StageName and `/query` with two records:
  `load()` returns fields `["Id","Name","StageName"]`, 2 cleaned records, Id present.
- `SalesforceObjectSource` Id-not-queryable describe -> `load()` raises `IngestionError` with
  `status_code == 422`.

## Constraints
- `from __future__ import annotations`; type hints; one-line docstrings.
- ingestion imports `app.domain.*` + `app.salesforce.*` + stdlib/httpx only. No fastapi/app.main.
- Move salesforce code verbatim; do not change its logic.

## Done criteria
- `python -m pytest -q` green; the Salesforce MockTransport test in test_scanner.py still passes.
- Report files created/moved/deleted + results.
