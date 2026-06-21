# Master Architecture Plan — Salesforce Data Anomaly Detector

Owner model: **Claude is the architect/planner/reviewer. Grok is the implementer.**
Claude writes plans here, drives Grok per phase, then reviews every diff and sends rework back to Grok.

## 1. What we are building

A trustworthy, coverage-aware Salesforce data **anomaly / data-quality detector**. It ingests
records (CSV export or direct Salesforce REST/SOQL), profiles them, grades how much of the data it
could actually inspect (coverage), runs a pluggable set of **detectors** that emit explainable
**findings**, persists an immutable scan manifest + findings, and lets a human reviewer label them.

Guiding principle from the existing docs: **missing access is a coverage failure, never evidence
that a value is blank.** Findings are *candidates for human review*, not verdicts.

## 2. Why we are refactoring

The baseline (`app/main.py`) is a single 445-line module mixing: DB schema, profiling, coverage,
detection rules, persistence, orchestration, and HTTP routing. Adding detectors or swapping the
data source means editing the monolith. We move to a layered architecture so new detectors and new
sources are additive, and each concern is independently testable.

## 3. Target architecture (layered, dependencies point inward)

```
app/
  domain/            # Pure data + vocabulary. No I/O, no framework imports.
    models.py        #   Record, ScanRequest, Candidate, Finding, Coverage, Profile, ScanManifest
    enums.py         #   Severity, FindingStatus, CoverageGrade, FeedbackLabel, DetectorId
  policy/            # Configuration of WHAT is valid (separate from HOW we detect).
    field_policy.py  #   per-object expected fields, valid Opportunity stages, severity/priority map
  detection/         # HOW we detect. Strategy pattern. Open for extension.
    base.py          #   Detector Protocol: detect(ctx: DetectionContext) -> list[Candidate]
                     #   DetectionContext: object_name, records, fields, profile, policy
    registry.py      #   DetectorRegistry — ordered, named, enable/disable
    rules/           #   deterministic detectors (one class per rule family)
    statistical/     #   IQR / MAD / z-score outliers, rare-combination
  profiling/
    profiler.py      #   null-rate, distinct, top-values, numeric summary
  coverage/
    grader.py        #   CoverageGrader: good | partial | blocked + suppressed checks (policy-driven)
  ingestion/         # WHERE data comes from. Source abstraction.
    base.py          #   DataSource Protocol -> load() -> LoadedDataset(object_name, fields, records, origin)
    csv_source.py
    salesforce_source.py
  salesforce/        # moved client (was app/salesforce.py)
    session.py       #   SalesforceSession + session_from_environment
    client.py        #   SalesforceClient (REST/Describe/Tooling/Metadata SOAP)
    verify.py        #   verify_direct_api_access
  persistence/       # Repository pattern over SQLite. Hides SQL from services.
    database.py      #   connection ctx mgr + schema migration
    repositories.py  #   ScanRepository, CoverageRepository, FindingRepository, FeedbackRepository
  services/          # Orchestration. Depends on abstractions only.
    scan_service.py  #   ingest -> profile -> grade coverage -> detect -> persist -> assemble result
    feedback_service.py
    dashboard_service.py
  api/               # Thin HTTP. FastAPI routers + request/response DTOs.
    schemas.py
    routes_scans.py / routes_findings.py / routes_salesforce.py / routes_dashboard.py
  main.py            # Composition root: build app, instantiate repos/services, mount routers + static.
  static/            # unchanged frontend
```

Dependency rule: `domain` depends on nothing. `policy`, `profiling`, `detection`, `coverage`
depend only on `domain`. `persistence`, `ingestion`, `salesforce` depend on `domain`. `services`
depend on the abstractions (repository + detector + source interfaces). `api` depends on services.
`main.py` is the only place concrete implementations are wired together (Dependency Inversion).

## 4. SOLID mapping (the bar Grok must hit)

- **SRP** — every module has one reason to change (a detector, a repo, a route group).
- **OCP** — new detector = new class registered in the registry; the engine never changes.
- **LSP** — any Detector / DataSource / Repository is substitutable behind its Protocol.
- **ISP** — small Protocols (Detector, DataSource, each Repository) — no fat interfaces.
- **DIP** — services receive dependencies via constructor; concretes wired only in `main.py`.

## 5. Hard compatibility constraints (must not break)

- All existing HTTP endpoints keep identical paths, methods, and JSON shapes:
  `/`, `/tutorial`, `/api/health`, `/api/scans/demo`, `/api/scans/salesforce`,
  `/api/scans/upload`, `/api/scans`, `/api/scans/{id}`, `/api/findings`,
  `/api/findings/{id}/feedback`, `/api/salesforce/verify`, `/api/dashboard`.
- The existing test file `tests/test_scanner.py` must stay **green** at every phase boundary.
- Finding fingerprints stay stable (`sha256` over {object, record, rule, field}) so reviewer
  feedback survives re-scans. Idempotent re-scan (upsert by fingerprint) preserved.
- SQLite schema column names preserved (or migrated compatibly).

## 6. Phase order (each phase = one or more Grok tasks + a Claude review gate)

1. Domain + Policy (pure foundation, additive).
2. Detection engine + port existing 7 checks into detectors (+ unit tests).
3. Profiling + Coverage modules.
4. Persistence repositories + database module.
5. Ingestion sources + relocate Salesforce client.
6. Services + composition root; rewrite `main.py` thin; keep endpoints identical; tests green.
7. API routers + DTOs split.
8. New value-add detectors: orphan/referential, staleness, duplicate, cross-field consistency
   (e.g. CloseDate in the past while stage is open), MAD/z-score outliers.
9. Full verification: pytest + API smoke + SOLID review.

## 7. Review protocol (Claude, after each Grok run)

1. `git diff` the changes; read every new/changed file.
2. Check: layi­ng respected (no inward dep violations), Protocol used not concrete, names match
   plan, no behavior regressions, type hints + docstrings present, no secrets logged.
3. Run `pytest`. If red or design is off, write a precise rework instruction and re-drive Grok
   (`grok -c -p ...`) — do not hand-fix unless trivial.
4. On green + clean: `git commit` the phase, tick the Task, update memory if a lasting fact emerged.

See `.claude/skills/drive-grok/SKILL.md` for the exact Grok CLI workflow.
