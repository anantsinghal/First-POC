# Phase 49 — Salesforce Bulk API 2.0 extraction (mock-tested streaming source)

## Why
REST/SOQL pagination is capped and memory-bound. Bulk API 2.0 is Salesforce's mechanism for large
extractions: create an async query job, poll until complete, then download results as CSV pages via
the `Sforce-Locator` header. This phase adds it as a **streaming source** that feeds the Phase-48
`stream_scan` — completing the live-scale extraction story. Fully mock-transport tested (no creds).

HARD CONSTRAINTS: zero changes under `app/detection/`, `app/profiling/`, `app/coverage/`,
`app/planning/`, `app/policy/`. No new dependencies. Existing tests stay green.

## 1. `app/salesforce/client.py` — Bulk API 2.0 methods on `SalesforceClient`
Base: `{instance_url}/services/data/{api_version}/jobs/query`.
- `async def bulk_create_query_job(self, soql: str) -> str`: POST JSON
  `{"operation": "query", "query": soql}` → returns the job id from the response JSON (`id`).
- `async def bulk_job_status(self, job_id: str) -> str`: GET `/jobs/query/{job_id}` → returns
  `state` (e.g. "UploadComplete", "InProgress", "JobComplete", "Failed", "Aborted"). Raise
  `SalesforceError` on "Failed"/"Aborted" including any `errorMessage` in the body.
- `async def bulk_job_results_pages(self, job_id: str, *, max_records: int) -> AsyncIterator[str]`:
  async generator yielding raw CSV text pages. First GET `/jobs/query/{job_id}/results` with header
  `Accept: text/csv`; read the `Sforce-Locator` response header; while a locator exists and it isn't
  `"null"` and record budget remains, GET again with `?locator=<locator>` and yield each page's text.
  (Track an approximate record budget by counting newlines minus header per page; stop when
  `max_records` reached.)
  NOTE: `_request` returns the full response — reuse it; the pages are text (`response.text`).

## 2. New source — `app/ingestion/salesforce_bulk_source.py` — `class SalesforceBulkSource(DataSource)`
`__init__(self, *, client, session, object_name, max_records: int = 1_000_000,
poll_interval: float = 2.0, max_polls: int = 60, batch_size: int = 5_000, fields: list[str] | None
= None)`.
- Validate `object_name` with the existing `SAFE_API_NAME` regex (import from `app.salesforce`) else
  `IngestionError(400)`.
- `async def _prepare(self)`: describe the object (same describe-driven field selection as
  `SalesforceObjectSource` — REUSE by importing/extracting the selection logic if cleanly possible,
  else duplicate the small filter loop with the same EXCLUDED_FIELD_TYPES/MAX_QUERY_FIELDS constants
  imported from `salesforce_source`); build `self.schema` (via `schema_from_salesforce_describe`) and
  `self.fieldnames = ["Id", *others]`; create the job (`SELECT fields FROM object`), poll
  `bulk_job_status` up to `max_polls` times with `await asyncio.sleep(self.poll_interval)` between
  (`poll_interval=0` in tests → no real sleeping) until "JobComplete" (else IngestionError 502 with
  the state).
- `def iter_batches(...)` cannot be async-consumed by stream_scan today — CHECK how stream_scan
  consumes iter_batches (synchronous generator). Bulk pages arrive via async HTTP. Bridge: implement
  `async def load(self)` (materialising up to max_records — the compat path) AND an
  `async def aiter_batches(self, batch_size)` async generator. Then EXTEND `stream_scan` minimally:
  if the source has `aiter_batches`, consume with `async for`; elif `iter_batches`, consume the sync
  generator (existing behavior). Keep both paths sharing the same per-batch body via a small inner
  function.
- Page → records: parse each CSV page with `csv.DictReader(io.StringIO(page))` (each page includes a
  header row per Bulk API 2.0), clean values (`clean(...)`, None→""), ensure `Id` first in fields;
  accumulate rows across pages and yield lists of `batch_size`; yield the remainder; respect
  `max_records` and set `self.truncated` when the cap was hit.
- No related-id resolution in v1 bulk (leave `self.related_ids = None`); source_name
  `f"salesforce-bulk:{host}/{object}"`.

## 3. Endpoint — `app/api/routes_salesforce.py` (or routes_scans.py, follow existing layout)
`POST /api/scans/salesforce/bulk` with the existing `SalesforceScanInput` body but allow
`max_records` up to 1_000_000 for this endpoint (define a `BulkScanInput(BaseModel)` in
`app/api/schemas.py`: object_name same pattern; `max_records: int = Field(default=100_000, ge=1,
le=1_000_000)`). Build session/client from env (same error mapping as `/api/scans/salesforce`),
`SalesforceBulkSource(...)`, and return `await container.scan_service.stream_scan(source)`.

## 4. Tests — `tests/test_bulk_api.py` (NEW)
Use a fake `SalesforceSession` + `httpx.MockTransport` (no creds). The handler simulates:
- describe for "Account" (Id, Name required, Type picklist Customer/Partner, Website url — all queryable),
- `POST .../jobs/query` → `{"id": "750XX", "state": "UploadComplete"}`,
- `GET .../jobs/query/750XX` → first call "InProgress", second+ "JobComplete" (stateful via a counter
  in the handler closure),
- `GET .../jobs/query/750XX/results` (no locator) → CSV page 1 with header + 3 rows (one row with
  Type="Frozen", one with Name=""), response header `Sforce-Locator: LOC2`,
- with `?locator=LOC2` → CSV page 2 with header + 2 clean rows and `Sforce-Locator: null`.
1. Client unit: create job → id; status polls InProgress→JobComplete; results pages yields 2 pages.
2. Source: `SalesforceBulkSource(..., poll_interval=0)`; collect `aiter_batches(batch_size=2)` via
   asyncio; assert 5 records total across batches, batches of ≤2, fields start with Id, schema has
   the picklist, truncated False.
3. End-to-end stream_scan: run `stream_scan(source)` via a tmp container; assert scan completed,
   findings include `picklist_invalid` (Frozen) and `required_missing` (empty Name); audit
   `scan_streamed` present.
4. Failure: handler returning state "Failed" with errorMessage → IngestionError/SalesforceError
   surfaces as a clean error (assert raises).
5. Truncation: max_records=3 → truncated True → coverage partial ("record cap").

## 5. Docs
README API list + one line in `docs/scalability.md` roadmap: Bulk API 2.0 extraction ✅ (mock-tested;
live tuning pending real org).

## Done criteria
Full suite + healthcheck green. Report files changed, test tails, zero engine diffs, and the
end-to-end bulk→stream_scan finding types.
