# Phase 4 — Persistence (Repository pattern)

Encapsulate ALL SQLite access behind a `Database` + four repositories. Services (later phases) must
never write SQL. The SQLite schema and stored values must stay **identical** to baseline
`app/main.py` so the existing DB shape and API JSON are preserved. Do NOT modify `app/main.py`,
`app/salesforce.py`, or `tests/test_scanner.py`.

## `app/persistence/__init__.py`
Empty package marker.

## `app/persistence/database.py`
- `def now() -> str` -> `datetime.now(UTC).isoformat()` (UTC import from datetime).
- `def serialise_row(row: sqlite3.Row) -> dict[str, Any]` — same as baseline: `dict(row)`, then for
  each key in `("fields_json","profile_json","queried_fields_json","missing_fields_json",
  "suppressed_checks_json","evidence_json")` present and not None, `json.loads` it into the key with
  `_json` removed. Returns the dict.
- `class Database`:
  - `__init__(self, db_path: Path)` stores the path (no env reads here — path is injected).
  - `@contextmanager def connect(self) -> Iterator[sqlite3.Connection]`: ensure parent dir exists
    (`self.db_path.parent.mkdir(parents=True, exist_ok=True)`), open `sqlite3.connect(self.db_path)`,
    set `row_factory = sqlite3.Row`, `yield`, `commit()` on success, `close()` in finally.
  - `def setup(self) -> None`: open a connection and `executescript` the EXACT baseline schema
    (tables `scan_manifests`, `coverage`, `findings`, `feedback` + the two indexes, all `IF NOT
    EXISTS`, column names unchanged from `app/main.py setup_database`).

## `app/persistence/repositories.py`
Each repository method takes an open `sqlite3.Connection` as its first arg (unit-of-work: the
service opens one connection and passes it in, so a scan write stays atomic). Read methods return
serialised dicts (via `serialise_row`) or plain values; write methods accept domain objects.

`class ScanRepository`:
- `insert_running(conn, *, scan_id, source_name, object_name, created_at, row_count, source_hash,
  fields) -> None` — INSERT into scan_manifests with `status='running'`,
  `policy_version='poc-policy-v1'`, `parser_version='poc-parser-v1'`, `fields_json=json.dumps(fields)`.
- `complete(conn, *, scan_id, completed_at, profile, notes) -> None` — UPDATE status='completed',
  completed_at, `profile_json=json.dumps(profile)` (profile is the dict from `Profile.to_dict()`), notes.
- `get(conn, scan_id) -> dict | None` — SELECT * ... serialise_row.
- `list(conn, limit=50) -> list[dict]` — `ORDER BY created_at DESC LIMIT ?`, serialised.
- `latest_id(conn) -> str | None` — id of most recent by created_at.
- `count(conn) -> int`.

`class CoverageRepository`:
- `insert(conn, scan_id, coverage: Coverage) -> None` — INSERT row using coverage fields:
  observed_records, `queried_fields_json=json.dumps(coverage.queried_fields)`, missing_fields_json,
  suppressed_checks_json, `grade=coverage.grade.value`, reason.
- `get(conn, scan_id) -> dict | None` — serialised.

`class FindingRepository`:
- `upsert(conn, *, scan_id, candidate: Candidate, source_hash, scan_created_at) -> None` —
  replicate baseline upsert EXACTLY:
  - `existing = SELECT id, status FROM findings WHERE fingerprint = candidate.fingerprint`.
  - If existing: UPDATE scan_id, record_name, severity (`candidate.severity.value`), confidence,
    priority, title, `evidence_json=json.dumps(candidate.evidence)`, source_hash,
    `last_seen_at=now()`, `status = 'reopened' if existing['status']=='resolved' else
    existing['status']` WHERE id=existing id.
  - Else: INSERT new with `id=str(uuid4())`, fingerprint, scan_id, object_name, record_id,
    record_name, finding_type, field_name, `severity=candidate.severity.value`, confidence, priority,
    `status='open'`, title, evidence_json, source_hash, `created_at=scan_created_at`,
    `last_seen_at=scan_created_at`.
- `list(conn, *, scan_id: str | None = None, status: str | None = None) -> list[dict]` — same WHERE
  building as baseline, `ORDER BY priority DESC, last_seen_at DESC`, serialised (no feedback join here).
- `get(conn, finding_id) -> dict | None`.
- `exists(conn, finding_id) -> bool`.
- `count_for_scan(conn, scan_id) -> int`.
- `count_open(conn) -> int` — `WHERE status IN ('open','reopened')`.
- `set_status(conn, finding_id, status: str) -> None`.

`class FeedbackRepository`:
- `insert(conn, *, finding_id, label: str, note: str, reviewer: str) -> str` — INSERT with
  `id=str(uuid4())`, created_at=now(); return the new id.
- `list_for_finding(conn, finding_id) -> list[dict]` — `ORDER BY created_at DESC`, each `dict(row)`.

## Tests — `tests/test_persistence.py` (NEW)
Use a `Database(tmp_path/'t.db')`; `setup()`. In one `connect()`:
- insert_running + CoverageRepository.insert(Coverage(...GOOD)) + FindingRepository.upsert(Candidate)
  then ScanRepository.complete. Reopen connection and assert: `get(scan_id)['status']=='completed'`,
  coverage `get` grade 'good', `count_for_scan==1`, finding severity stored as the plain string
  (e.g. 'high'), `evidence` round-trips via serialise (list returns dict with parsed `evidence`).
- Upsert idempotency: upsert the same Candidate twice -> still one row (`count_for_scan==1`).
- Reopen logic: set_status(finding,'resolved'); upsert same candidate again -> status becomes
  'reopened'.
- FeedbackRepository.insert then list_for_finding returns 1 row; set_status reflects.

## Constraints
- `from __future__ import annotations`; type hints; one-line docstrings.
- persistence imports stdlib + `app.domain.*` only (for Candidate/Coverage). No fastapi/httpx/app.main.
- Don't touch existing files except adding the new test.

## Done criteria
- `python -m pytest -q` green. Report files + results.
