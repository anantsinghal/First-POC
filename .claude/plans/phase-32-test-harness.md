# Phase 32 — Reusable testing harness + single system healthcheck

Goal: a reusable harness so every future change is verified by running ONE command with compact output
(saving tokens), and new detectors/features need only a few lines to test. Additive — do NOT modify or
delete existing tests; they keep running. The harness consolidates the patterns we've used repeatedly
(build context, run registry, assert invariants, run via ScanService, TestClient with tmp dir, mock
Salesforce).

## 1. `tests/harness.py` — reusable utilities (NOT a test file; no `test_` functions)
Builders:
- `ROOT` = repo root (`Path(__file__).resolve().parents[1]`).
- `def demo_records() -> tuple[list[dict], list[str]]` — load `data/demo_opportunities.csv` (utf-8-sig,
  DictReader, `clean`) → (records, fields).
- `def csv_bytes(headers: list[str], rows: list[list[str]]) -> bytes` — build CSV bytes.
- `def make_schema(object_name: str, fields: list[dict]) -> ObjectSchema` — thin wrapper over
  `schema_from_dict({"object_name":object_name,"fields":fields})`.
- `def context(object_name, records, *, fields=None, schema=None, related_ids=None,
  related_records=None, policy=DEFAULT_POLICY) -> DetectionContext` — `fields` defaults to the keys of
  the first record.
- `def detect_all(ctx) -> list[Candidate]` — `build_default_registry().detect(ctx)`.
- `def type_counts(findings) -> dict[str,int]` — Counter of finding_type.

Assertions (raise AssertionError with clear messages):
- `def assert_finding_contract(f) -> None` — severity.value in {high,medium,low}; 0<=confidence<=1;
  0<=priority<=1; record_id non-empty; evidence is dict with "why"; `json.dumps(f.evidence)` ok.
- `def assert_healthy(findings) -> None` — unique fingerprints + `assert_finding_contract` for each.

Service + API + Salesforce-mock helpers:
- `class FakeSource:` `__init__(self, dataset)`, `async def load(self): return self._dataset`.
- `def run_scan(dataset, data_dir: Path) -> tuple[dict, list[dict]]` — build a fully-wired container via
  `app.api.container.build_container(data_dir=data_dir, root=ROOT)`, `container.database.setup()`,
  `scan = asyncio.run(container.scan_service.run_scan(FakeSource(dataset)))`, `findings =
  container.finding_service.list_findings(scan_id=scan["id"])`; return `(scan, findings)`.
- `@contextmanager def api_client(data_dir: Path)` — set `os.environ["SCANNER_DATA_DIR"]=str(data_dir)`,
  `importlib.reload(app.main)`, `with TestClient(app.main.app) as c: yield c`.
- `def salesforce_source(object_name, *, describe: dict, query_records: list[dict],
  parent_existing: dict[str, set[str]] | None = None, **kwargs) -> SalesforceObjectSource` — returns a
  `SalesforceObjectSource` whose `SalesforceClient` uses an `httpx.MockTransport` that answers:
  the `/sobjects/{object}/describe` GET with `describe`; the main `/query` (SELECT ... FROM object) with
  `query_records`; and any `SELECT Id FROM <Parent> WHERE Id IN (...)` by echoing the ids in the WHERE
  clause that are in `parent_existing[Parent]` (default: all present). Uses a fake
  `SalesforceSession("https://harness.my.salesforce.com","FAKE_TOKEN","v66.0")`. NO real credentials.

## 2. `tests/conftest.py` — pytest fixtures
- `@pytest.fixture def data_dir(tmp_path) -> Path` → `tmp_path`.
- `@pytest.fixture def client(tmp_path)` → use `harness.api_client(tmp_path)` (yield the TestClient).

## 3. `tests/test_healthcheck.py` — ONE authoritative system health test using the harness
`def test_system_healthcheck(tmp_path)`:
- **CSV demo**: `recs, fields = demo_records()`; `f = detect_all(context("Opportunity", recs, fields=fields))`;
  assert `len(f)==10`; `assert_healthy(f)`.
- **Rich schema (all families)**: reuse the rich 60-record schema'd fixture (same as test_integration —
  may import a small builder or inline); `f = detect_all(ctx)`; assert `type_counts` keys ⊇
  {numeric_outlier, robust_outlier, rare_combination, pattern_break, ml_anomaly, required_missing,
  picklist_invalid, reference_missing, invalid_format, orphan_reference, missing_contract_closed_won};
  `assert_healthy(f)`.
- **Multi-object**: parents (20 Accounts) + related Contacts (17 linked) via `context(..., related_records=
  {"Contact": contacts})`; assert `missing_children` count == 3; `assert_healthy(f)`.
- **Salesforce mock**: `src = salesforce_source("Account", describe=<Id/Name(req)/Type(picklist)/
  Website(url)>, query_records=[clean, bad-picklist+bad-url, empty-name])`; `ds = asyncio.run(src.load())`;
  `f = detect_all(context("Account", ds.records, fields=ds.fields, schema=ds.schema,
  related_ids=ds.related_ids))`; assert {picklist_invalid, invalid_format, required_missing} ⊆ types.
- **API smoke** via `harness.api_client(tmp_path)`: GET /api/health == ok; POST /api/scans/demo 200 +
  finding_count 10; GET /api/findings?scan_id=... non-empty; GET /api/dashboard 200; GET /api/audit
  includes a scan_completed entry.

## Going-forward rule (document briefly at top of harness.py docstring)
"Add a detector → add its expected type to the healthcheck families set + a focused test using these
helpers. If a change breaks the healthcheck, fix the logic; if the harness can't express a case, extend
the harness."

## Constraints
- `from __future__ import annotations`; type hints; docstrings. NO real credentials (mock transport).
- Do not modify existing tests. `python -m pytest -q` must stay green; the new healthcheck must pass.

## Done criteria
- `python -m pytest -q` green including `tests/test_healthcheck.py`. Report files created + results, and
  confirm `pytest tests/test_healthcheck.py -q` runs the full end-to-end health gate.
