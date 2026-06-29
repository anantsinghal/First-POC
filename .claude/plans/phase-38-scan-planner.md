# Phase 38 — Scan planner + detector tiering

## Why
At "hundreds of objects, millions of records" we must not naively run every detector on every object.
Cheap metadata checks should run everywhere; expensive detectors (ML, pattern-mining) must be gated by
size/budget and, when deferred, **reported honestly** (never silently skipped). This phase adds a
planner that decides which detectors run for a given object/size, filters the registry accordingly,
and records what was deferred and why. It is additive and backward-compatible: the registry's default
behavior (all detectors) is unchanged; only the scan-service path becomes plan-aware.

Reusability invariant: the planner is generic (keyed by detector id + record count + config), so it
works for any object in any org with no per-object code.

## 1. Tier map + planner — new package `app/planning/` (`__init__.py` empty)

### `app/planning/planner.py`
- `DETECTOR_TIERS: dict[str, int]` mapping each detector `id` to a tier (0 cheapest):
  - **Tier 0** (single-pass, always run): `required_missing`, `picklist_invalid`, `reference_missing`,
    `invalid_format`, `rule_interpreter`.
  - **Tier 1** (moderate; gate above `tier1_max_records`): `numeric_outlier`, `robust_outlier`,
    `rare_combination`, `duplicate_name`, `orphan_reference`, `missing_children`.
  - **Tier 2** (expensive; gate above `ml_max_records` / `pattern_max_records`): `pattern_break`,
    `ml_anomaly`.
  - Any detector id NOT in the map defaults to Tier 0 (fail-safe: unknown detectors still run cheaply).
- `@dataclass(frozen=True) class ScanPlan`:
  - `object_name: str`, `estimated_records: int`, `included_ids: frozenset[str]`,
    `skipped: tuple[tuple[str, str], ...]` (list of (detector_id, reason)), `notes: str`.
- `@dataclass(frozen=True) class ScanPlanner`:
  - Config fields (all with defaults): `tier1_max_records: int = 2_000_000`,
    `pattern_max_records: int = 500_000`, `ml_max_records: int = 200_000`.
  - `def plan(self, object_name: str, estimated_records: int) -> ScanPlan`:
    - Walk every id in `DETECTOR_TIERS` (plus treat unknown as tier 0 when filtering later). For each,
      decide include vs skip by tier threshold:
      - tier 0 → always include.
      - tier 1 → include if `estimated_records <= tier1_max_records`, else skip with reason
        `f"tier-1 detector deferred: {estimated_records} records exceeds {tier1_max_records} (needs columnar/streaming)"`.
      - tier 2 `pattern_break` → include if `<= pattern_max_records` else skip with reason
        `f"pattern mining deferred: {estimated_records} exceeds {pattern_max_records} (needs sampling)"`.
      - tier 2 `ml_anomaly` → include if `<= ml_max_records` else skip with reason
        `f"ML scoring deferred: {estimated_records} exceeds {ml_max_records} (needs sampling)"`.
    - `included_ids` = frozenset of included ids. `notes` = a short human summary (e.g.
      `"3 detector(s) deferred for size"` or `"all detectors planned"`).
  - `def applies(self, detector_id: str, plan: ScanPlan) -> bool`: helper — `detector_id in
    plan.included_ids or detector_id not in DETECTOR_TIERS` (unknown ids always allowed/cheap).

## 2. Registry filter — `app/detection/registry.py`
Add an optional include filter to `DetectorRegistry.detect`:
`def detect(self, ctx, *, include: set[str] | frozenset[str] | None = None) -> list[Candidate]`.
When `include` is None → run all detectors (UNCHANGED). When provided → only run detectors whose
`getattr(det, "id", None)` is in `include` OR whose id is None (defensive). Always `reconcile(...)` the
result as today. (This keeps `build_default_registry().detect(ctx)` behavior identical for all existing
callers/tests.)

## 3. Service wiring — `app/services/scan_service.py`
- `ScanService.__init__` gains `planner: ScanPlanner` (store as `self.planner`).
- In `run_scan`, after computing coverage and BEFORE detection (in the non-blocked branch):
  - `plan = self.planner.plan(dataset.object_name, len(dataset.records))` (record count is the
    estimate; in production this comes from a pre-extraction count — note this in a comment).
  - `candidates = self.registry.detect(ctx, include=plan.included_ids)`.
- Keep the BLOCKED branch as-is (`candidates = []`, no plan needed).
- Reflect the plan honestly:
  - `complete(...)` notes: `f"{len(candidates)} candidate occurrences generated. {plan.notes}"`.
  - Audit `scan_completed` detail: add `"deferred_detectors": [id for id, _ in plan.skipped]` and
    `"planned_detectors": len(plan.included_ids)`.

## 4. Composition — `app/api/container.py`
Build `planner = ScanPlanner()`; pass `planner=planner` into `ScanService`. (No new endpoint needed.)

## 5. Tests — `tests/test_scan_planner.py` (NEW)
- Planner unit:
  - `plan("Account", 10)` → `included_ids` contains every tier (ml_anomaly + pattern_break included),
    `skipped == ()`.
  - `plan("Account", 5_000_000)` → `ml_anomaly` and `pattern_break` NOT in `included_ids`, and also
    tier-1 ids deferred (5M > tier1_max 2M); `skipped` non-empty with readable reasons; tier-0 ids
    (e.g. `required_missing`) still included.
  - `plan("Account", 300_000)` → ml_anomaly deferred (>200k) but pattern_break included (<500k) and
    tier-1 included.
- Registry filter unit (via harness): `harness.detect_all(ctx)` (no include) unchanged; a filtered run
  excluding `ml_anomaly`/`pattern_break` ids on the rich context yields findings without those types.
- Service path: build a `ScanService` (or use the container) with a planner whose `ml_max_records=5`
  and `pattern_max_records=5`; run the demo CSV via the API or `harness.run_scan` — assert the scan
  completes, demo finding count is the SAME (ml/pattern fire 0 on 11 rows anyway), and an audit
  `scan_completed` entry lists `ml_anomaly`/`pattern_break` in `deferred_detectors`.
  - For this, either expose planner config through the container test or construct ScanService directly
    in the test with a custom ScanPlanner (preferred — construct via build_container then replace, or
    build the service deps directly). Simplest: build a container, then call
    `container.scan_service.planner` is frozen; instead construct a ScanService in the test using the
    container's repos + a custom planner. Use whatever is cleanest; the assertion that matters is
    "deferred_detectors includes ml_anomaly + pattern_break when thresholds are tiny."
- Demo regression: standard demo scan (default planner) still yields 10 findings.

## Constraints
- `from __future__ import annotations`; type hints; docstrings. No hardcoded object/field names.
- Existing tests/healthcheck stay green; `build_default_registry().detect(ctx)` behavior unchanged.

## Done criteria
- `python -m pytest -q` green including the new planner tests. Report files + results, and confirm that
  a tiny-threshold planner defers ml_anomaly/pattern_break (recorded in audit) while a normal demo scan
  is unchanged.
