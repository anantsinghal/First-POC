# CLAUDE.md — Salesforce Data Quality / Anomaly Detector

Guidance for AI assistants working in this repo.

## What this is
A coverage-aware Salesforce data **anomaly / data-quality detector** (FastAPI). It ingests records
(CSV export or direct Salesforce REST/SOQL — no `sf`/`sfdx` CLI), profiles them, grades coverage,
runs pluggable **detectors** that emit explainable **findings** (candidates for human review),
persists an immutable scan manifest + findings in SQLite, and lets a reviewer label findings.
Core principle: **missing access is a coverage failure, never proof a value is blank.** Findings are
candidates, not verdicts.

## Architecture (layered, SOLID; deps point inward)
`domain` (pure) <- `policy` / `profiling` / `coverage` / `detection` <- `ingestion` / `salesforce` /
`persistence` <- `services` <- `api` <- `main.py` (composition root / app factory). Only `api/` and
`main.py` import FastAPI. See README "Architecture" and `.claude/plans/00-master-architecture.md`.

### Adding a new anomaly check (the common task)
1. Write a detector class in `app/detection/rules/` or `app/detection/statistical/` with a class
   attr `id` and `def detect(self, ctx: DetectionContext) -> list[Candidate]`, using
   `make_candidate(...)` from `app/detection/base.py`.
2. Add its `(Severity, confidence, priority)` row to `FieldPolicy.severity_for` in
   `app/policy/field_policy.py`.
3. Append it in `build_default_registry()` in `app/detection/registry.py`.
4. Add a unit test under `tests/`. Do NOT modify the engine, services, persistence, or API — the
   Strategy design means new checks are additive (Open/Closed).

## Conventions
- Detectors skip records with an empty `Id`; guard on `ctx.has(field)` / `ctx.is_opportunity()`.
- Finding fingerprint = `sha256` over `{object, record, rule, field}` — keeps reviewer feedback
  stable across re-scans. Re-scan is an idempotent upsert by fingerprint.
- Domain stays framework-free (no fastapi/httpx/sqlite). Repositories own all SQL.
- `severity` is stored as the enum's `.value` string; API JSON shapes must stay stable.

## Run / test
```powershell
uvicorn app.main:app --reload    # http://127.0.0.1:8000  (demo scan button, or upload a CSV)
pytest                            # full suite
```

## Working style in this repo
This project is built with **Claude as architect/reviewer and the Grok CLI as implementer** — see
`.claude/skills/drive-grok/SKILL.md` and `.claude/plans/`. Keep `tests/test_scanner.py` green; it is
the API compatibility contract.
