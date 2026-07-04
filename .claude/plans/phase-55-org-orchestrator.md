# Phase 55 — Org-wide orchestrator + Bulk↔incremental join (last gap phase)

## Why
Today a human must enumerate objects and pick endpoints one at a time. This phase adds: (A)
`modified_since` support on the Bulk source + incremental/reconcile for streaming sources (so huge
objects get delta scans, not full re-pulls), and (B) an **org orchestrator**: one call that discovers
every object, sizes it, routes small→in-memory / large→bulk-stream, respects an API budget, and
returns an honest org summary (scanned vs skipped WITH reasons).

DEPENDS ON Phases 53–54 merged.
HARD CONSTRAINTS: zero changes under `app/detection/`, `app/profiling/`, `app/coverage/`,
`app/planning/`, `app/policy/`. No new deps. All existing tests pass unmodified.

## A. Bulk ↔ incremental join
1. `app/ingestion/salesforce_bulk_source.py`: add `modified_since: str | None = None` to
   `__init__`; in `_prepare()` append ` WHERE SystemModstamp >= {modified_since}` (unquoted ISO
   literal) to the bulk SOQL when set. Nothing else changes.
2. `app/services/sync_service.py`:
   - `incremental_scan(source, ...)`: when the source has `aiter_batches` (streaming/bulk), take the
     STREAMING path: set `source.modified_since` from the watermark (same overlap math), then wrap
     `source.aiter_batches` with a teeing async generator that, per batch, flushes ledger entries
     `[(r["Id"], stable_hash(r)) ...]` via `sync.ledger_upsert_many` on its OWN short `connect()`
     (bounded memory — never accumulates records), then `scan = await
     self.scan_service.stream_scan(source)` (stream_scan sees the wrapped aiter via monkey-assigning
     `source.aiter_batches = wrapper` before the call — implement carefully: capture the ORIGINAL
     bound method first). Watermark set + audit (`"mode": "incremental-bulk"` when streaming) as
     today. Non-streaming sources keep the existing load()+run_scan path unchanged.
   - `reconcile(source)`: for streaming sources, the wrapper additionally accumulates an
     **ids-only** set (strings; ~tens of MB even at 1M rows — acceptable, documented) used for the
     vanished-diff; everything else identical to today's reconcile.
3. Endpoint: `POST /api/scans/salesforce/bulk/incremental` taking the existing `BulkScanInput`,
   building the env session/client/`SalesforceBulkSource` like `/api/scans/salesforce/bulk` (same
   error mapping) and calling `sync_service.incremental_scan(source)`.

## B. Org orchestrator — `app/services/orchestrator_service.py` — `class OrgScanOrchestrator`
`__init__(self, *, scan_service, audit: AuditRepository, database)`.
- `SYSTEM_OBJECT_SUFFIXES = ("ChangeEvent", "Feed", "History", "Share", "Tag")` — generic Salesforce
  system child-object suffixes, skipped with reason "system child object" (documented; suffix-based,
  not name-based, so it stays org-agnostic).
- `async def scan_org(self, *, client, session, include: list[str] | None = None,
  exclude: list[str] | None = None, max_objects: int | None = None,
  stream_threshold: int = 10_000, small_max_records: int = 2_000,
  bulk_max_records: int = 1_000_000, api_budget: int | None = None) -> dict`:
  1. **Discover**: `await client.describe_global()`; keep sobjects with `queryable` true and not
     `deprecatedAndHidden`; apply suffix skip; apply include (exact-name allowlist) / exclude;
     sort custom objects first (they're the org's business core), then standard; cap at
     `max_objects` (record the rest as skipped "max_objects cap").
  2. **Size + route per object** (SEQUENTIAL — SQLite is single-writer and Salesforce API limits are
     real; documented): budget check first (each object costs ~3+ API calls; decrement a simple
     counter when `api_budget` set; when exhausted, remaining objects recorded skipped
     "api budget exhausted"). `SELECT COUNT() FROM <obj>` via `client.query_records`? COUNT() SOQL
     returns `totalSize` with no records — use a small helper `client.json("GET", "/query",
     params={"q": f"SELECT COUNT() FROM {obj}"})` and read `totalSize` (wrap try/except
     SalesforceError → skipped with the error reason). count == 0 → skipped "no records".
  3. count <= stream_threshold → `SalesforceObjectSource(..., max_records=small_max_records)` +
     `run_scan`; else → `SalesforceBulkSource(..., max_records=bulk_max_records)` + `stream_scan`.
     Wrap each per-object scan in try/except (IngestionError, SalesforceError, Exception) → recorded
     as `{"object", "error"}` in a `failed` list; NEVER abort the whole org for one object.
  4. **Summary** return: `{"objects_discovered": N, "scanned": [{object, scan_id, route
     ("memory"|"bulk-stream"), rows, findings, coverage_grade}], "skipped": [{object, reason}],
     "failed": [{object, error}], "total_findings": sum, "api_calls_used": n}`. Audit event
     `"org_scan"` with the count fields only.
- Container: build + expose `orchestrator_service`.
- Endpoint `POST /api/scans/salesforce/org` with `OrgScanInput(BaseModel)`: `max_objects: int | None
  = Field(default=25, ge=1, le=500)`, `stream_threshold: int = Field(default=10_000, ge=1)`,
  `include: list[str] | None = None`, `exclude: list[str] | None = None`, `api_budget: int | None =
  Field(default=None, ge=1)` (in `app/api/schemas.py`). Route builds env session/client (same error
  mapping as other salesforce routes) → `orchestrator_service.scan_org(...)`.

## C. Tests — `tests/test_orchestrator.py` (NEW; fake session + stateful httpx.MockTransport)
Handler simulates: `/sobjects` (describe_global) listing: `Small__c` (custom, queryable),
`Big__c` (custom, queryable), `Empty__c` (custom, queryable), `Broken__c` (custom, queryable),
`AccountChangeEvent` (queryable — must be suffix-skipped), `Hidden__c` (queryable=false);
per-object describes (Id + Name required + a picklist w/ values); COUNT queries → Small__c=40,
Big__c=25_000, Empty__c=0, Broken__c → HTTP 400; Small__c data query → 40 rows w/ one bad picklist;
Big__c bulk job lifecycle (create/poll/results 2 CSV pages, one bad-picklist row).
1. **Routing + resilience**: `scan_org(stream_threshold=10_000)` → summary has scanned entries for
   Small__c (route "memory") and Big__c (route "bulk-stream"), skipped contains Empty__c ("no
   records") + AccountChangeEvent (suffix) + Hidden__c (not queryable), failed contains Broken__c;
   total_findings >= 2; both scans exist via scan detail with completed status.
2. **Budget**: `api_budget=` a value that only allows the first object → exactly one scanned,
   remaining skipped with "api budget exhausted".
3. **Include filter**: `include=["Small__c"]` → only Small__c scanned.
4. **Bulk incremental**: set a watermark via a first `sync_service.incremental_scan(bulk_source)`
   (initial mode streams + ledgers ids per batch — assert ledger has the ids and mode
   "incremental-bulk" is audited on the SECOND call whose handler asserts the bulk SOQL contains
   "WHERE SystemModstamp >="), watermark advanced.
5. **API**: POST `/api/scans/salesforce/org` (harness.api_client + env monkeypatched? The route
   builds a session from env — monkeypatch `session_from_environment` OR set fake env vars +
   monkeypatch SalesforceClient transport... simplest: test the service directly for logic and test
   the ROUTE only for the no-creds 409 error path).

## D. Docs
README: `/api/scans/salesforce/org` + `/api/scans/salesforce/bulk/incremental` lines.
`docs/scalability.md`: roadmap item 6 org-wide scheduler → done (sequential v1, budget-aware);
remaining-at-scale note: parallel scans need PostgreSQL (single-writer SQLite), deferred with reason.

## Done criteria
Full suite + healthcheck green, all existing tests unmodified. Report files changed, tails, and
confirmations: routing (memory vs bulk-stream), one object's failure not aborting the org, budget
stop, and the bulk-incremental WHERE clause + ledger writes.
