# Phase 51 — Feedback-driven ranking (flywheel v1) + profile drift detection

## Why
Two blueprint phase-4 capabilities, both buildable/testable offline:
(A) **Ranking**: reviewer labels should change what surfaces first — finding types reviewers keep
rejecting sink; types they confirm rise. Simple, explainable Bayesian smoothing (no sklearn — this is
flywheel v1; a trained model comes when real labels accumulate).
(B) **Drift**: when an object's data behavior shifts between scans (null-rates jump, value sets
change, volume swings), say so — that's monitoring, not just auditing.

HARD CONSTRAINTS: zero changes under `app/detection/`, `app/profiling/`, `app/coverage/`,
`app/planning/`, `app/policy/`. No new deps. Existing tests + API shapes stay green (new keys are
additive only).

## A1. Repository — `app/persistence/repositories.py`
`FeedbackRepository.label_counts_by_type(conn) -> dict[str, dict[str, int]]`:
`SELECT f.finding_type, fb.label, COUNT(*) FROM feedback fb JOIN findings f ON f.id = fb.finding_id
GROUP BY f.finding_type, fb.label` → `{finding_type: {label: count}}`.

## A2. Ranking — `app/services/ranking_service.py` — `class RankingService`
`__init__(self, *, database, feedback: FeedbackRepository, min_labels: int = 5, weight: float = 0.3)`.
- `def type_acceptance(self) -> dict[str, dict]`: open connect(); from `label_counts_by_type` compute
  per type: `accepted = real_issue`, `rejected = false_positive + valid_exception`,
  `total = accepted + rejected` (needs_review ignored); Laplace-smoothed
  `acceptance = (accepted + 1) / (total + 2)`; include only types with `total >= self.min_labels`.
  Return `{type: {"acceptance": round(...,3), "labels": total}}`.
- `def rank(self, findings: list[dict]) -> list[dict]`: fetch `type_acceptance()` once; for each
  finding dict add `rank_score = round(min(1.0, max(0.0, priority + self.weight * (acceptance - 0.5))),
  4)` when its type is in the map else `rank_score = priority`; add `type_acceptance` key (the
  acceptance float or None). Return the list re-sorted by `rank_score DESC` (stable, then keep
  existing relative order). PURE addition — original keys untouched.

## A3. Wiring
- `FindingService.list_findings` gains optional injected `ranker` (constructor param
  `ranker: RankingService | None = None`): after attaching feedback, if `self.ranker` is not None
  return `self.ranker.rank(rows)` else rows (unchanged when None).
- `container.py`: build `ranking_service = RankingService(database=database, feedback=feedback)`;
  pass `ranker=ranking_service` into FindingService; also add `ranking_service` to Container.
- `MetricsService.overview()`: add key `"type_acceptance": <ranking map>` — inject
  `ranker: RankingService` into MetricsService (constructor) and call `type_acceptance()`.
  (Additive key; existing metric keys unchanged.)

## B1. Drift — `app/services/drift_service.py` — `class DriftService`
`__init__(self, *, database, scans: ScanRepository, audit: AuditRepository, null_rate_delta: float =
0.10, count_swing: float = 2.0, mean_shift: float = 0.5)`.
- `def compare(self, scan_id: str) -> dict`:
  - connect(); `manifest = scans.get(...)` (NotFoundError if missing);
    `prev_id = scans.previous_scan_id(conn, object_name, created_at)`.
  - If no prev → `{"scan_id":..., "object_name":..., "baseline_scan_id": None, "drifts": [],
    "drift_count": 0}`.
  - Load both manifests' `profile` (already deserialised by serialise_row). Guard None profiles.
  - Drift rules over the CURRENT profile's fields vs baseline (only fields present in both):
    - `null_rate` abs delta >= null_rate_delta → kind "null_rate_shift".
    - top_values: values in current top5 not in baseline top5 (compare value part only) → kind
      "new_top_values" (list them).
    - numeric mean: if both have numeric, `abs(cur - base) / max(1.0, abs(base)) >= mean_shift` →
      kind "mean_shift".
  - record_count: ratio `cur / max(1, base)` >= count_swing or <= 1/count_swing → kind
    "volume_swing" (field "_record_count").
  - Each drift item: `{"field", "kind", "before", "after", "why"}` (human-readable why).
  - When `drifts` non-empty: `audit.append(conn, event="drift_detected", actor="system",
    scan_id=scan_id, object_name=..., detail={"drift_count": n, "kinds": sorted({d["kind"]...})})`.
  - Return the dict with drifts + drift_count + baseline_scan_id.

## B2. API
`container.py`: build + expose `drift_service`. `routes_scans.py`: `GET /api/scans/{scan_id}/drift`
→ `try drift_service.compare(scan_id) except NotFoundError → 404`. Register after the quality route.

## C. Tests — `tests/test_ranking_drift.py` (NEW)
Ranking:
1. Unit: RankingService(min_labels=3, weight=0.3) over a tmp container; run demo scan; label THREE
   `rare_combination` findings as `false_positive` (fetch findings, filter type, post feedback via
   the finding service or API); then `list_findings` → every rare_combination finding has
   `rank_score < priority` and `type_acceptance < 0.5`; a type with no labels keeps
   `rank_score == priority`; the list is sorted by rank_score desc.
   (Build FindingService with the low-threshold ranker directly from container parts, or construct
   RankingService(min_labels=3) and call .rank() on container.finding_service.list_findings output —
   simplest and sufficient.)
2. Below threshold: with min_labels=5 and only 3 labels, rank() leaves scores == priority.
3. `/api/metrics` includes `type_acceptance` (dict, possibly empty).
Drift:
4. Via harness.api_client: upload CSV v1 (object "Widget__c": 20 rows, field `Color` filled, Amount
   ~100); then upload CSV v2 SAME object_name (40 rows — 2x volume; `Color` empty in half the rows —
   null_rate jump; Amount ~1000 — mean shift). GET `/api/scans/{scan2}/drift` → drift_count >= 3 with
   kinds ⊇ {"volume_swing", "null_rate_shift", "mean_shift"}; baseline_scan_id == scan1;
   GET audit includes `drift_detected`. First scan's drift → drift_count 0, baseline None.
5. 404 for unknown scan id.

## D. Docs
README: add `GET /api/scans/{id}/drift` + one line on feedback-driven ranking under trust loop.
`docs/universal-strategy.md` §7: flip "Supervised model trained on labels" row to "⏳ phase 4 —
ranking flywheel v1 (label-driven re-ranking) DONE; trained model still pending real labels".

## Done criteria
Full suite + healthcheck green; report files changed, test tails, zero engine diffs, and confirm:
labeled-down type sinks in the queue, and the v2 upload triggers the three drift kinds.
