# Phase 25 — Generic describe-driven field selection for Salesforce scans

BUG found via live-path mock test: `SalesforceObjectSource` queries a hardcoded Opportunity-centric
list (`DEFAULT_SF_POLICY_FIELDS`), so on any other object it pulls almost nothing (e.g. Account → only
Id, Name) and the schema-driven detectors have no data. Fix: derive the queried fields from the
object's own `describe` so the engine works on ANY object — while minimizing PII (skip long text /
compound / encrypted). Add a mock integration test of the full Salesforce path.

## 1. `app/ingestion/salesforce_source.py`
- Remove the hardcoded `DEFAULT_SF_POLICY_FIELDS` selection as the default. Add module constants:
  - `EXCLUDED_FIELD_TYPES = {"address","location","base64","encryptedstring","textarea","complexvalue","anytype"}`
    (long free-text / compound / binary / encrypted — excluded by default for PII-minimization).
  - `MAX_QUERY_FIELDS = 100`.
- `__init__`: replace `policy_fields` with an optional override `fields: list[str] | None = None`
  (default None → derive from describe). Keep `client`, `session`, `object_name`, `max_records`.
- In `load()`:
  - `describe = await client.describe_sobject(object_name)`; build `schema =
    schema_from_salesforce_describe(describe)` (unchanged).
  - If `self.fields` is provided, use it filtered to readable; else **derive**:
    `selected = [f["name"] for f in describe.get("fields", []) if f.get("queryable") and f.get("name")
    and (f.get("type") or "").lower() not in EXCLUDED_FIELD_TYPES and "." not in f["name"]]`.
  - Ensure `Id` is included and FIRST: if `"Id" not in selected` raise the existing
    `IngestionError("...cannot query Id...", status_code=422)`; otherwise move `Id` to the front.
  - Cap to `MAX_QUERY_FIELDS` (keep Id + the first N-1 others); if truncated, that's acceptable for the
    POC (note in a comment).
  - Build the SOQL `SELECT {', '.join(fields)} FROM {object_name}`, query (`max_records`), clean records
    to those fields (as today), compute source_name/source_hash (as today), return LoadedDataset with
    schema (as today).
- Keep behavior: still raises IngestionError(422) when Id isn't queryable; still cleans values to strings.

## 2. Tests
- `tests/test_salesforce_scan.py` (NEW) — mock the full path with `httpx.MockTransport`:
  - describe for "Account" returning queryable fields: Id(id), Name(string, nillable False),
    Type(picklist active Customer/Partner), Website(url), Industry(picklist active Tech), and ALSO a
    `Description(textarea)` field (to prove it is EXCLUDED from the query).
  - `/query` returns 3 records: one clean; one with Type="Frozen" + Website="not-a-url"; one with Name="".
  - Build `SalesforceObjectSource(client=SalesforceClient(session, transport=mock), session=session,
    object_name="Account")`, `load()`, assert: queried `fields` includes Id, Name, Type, Website,
    Industry and does NOT include Description; records pulled = 3.
  - Run `build_default_registry().detect(DetectionContext("Account", ds.records, ds.fields,
    DEFAULT_POLICY, schema=ds.schema))` and assert findings include `picklist_invalid` (Type=Frozen),
    `invalid_format` (Website), and `required_missing` (empty Name).
  - Use a fake `SalesforceSession("https://carhuborg.my.salesforce.com","FAKE_TOKEN","v66.0")` — no real
    credentials; the mock transport intercepts all HTTP.
- `tests/test_ingestion.py` — if its existing Salesforce-source test asserted the OLD hardcoded field
  list, UPDATE only that test to reflect describe-derived selection (this is the one existing test that
  legitimately changes behavior). Keep the Id-not-queryable→422 case. Do not touch other tests.

## Constraints
- `from __future__ import annotations`; type hints; docstrings.
- CSV path and all other detectors/tests unchanged. No secrets used anywhere (mock transport only).

## Done criteria
- `python -m pytest -q` green. Report files changed + results, and confirm the mocked live Account scan
  now yields picklist_invalid + invalid_format + required_missing (proving generic field selection works).
