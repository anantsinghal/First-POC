# Phase 56 — Supervised feedback ranking model (the learning flywheel foundation)

## Why
Reviewer labels are the moat. Today ranking uses a per-type Laplace acceptance heuristic
(`RankingService`). This phase adds a real trained model: predict **P(a human accepts this finding)**
from its features, and rank by it — but ONLY when there is enough labeled data, else fall back to the
heuristic unchanged. Buildable/testable now on synthetic labels; activates automatically once real
labels accumulate.

DESIGN PRINCIPLES (non-negotiable):
- **Cold-start guard**: never train / never use a model below label thresholds; fall back to the
  existing heuristic. A data-starved model must never make ranking worse.
- **Explainable**: logistic regression with named features; expose top contributing features per
  prediction.
- **Safe + light inference**: persist the trained model as JSON (feature names + per-feature
  mean/std + coefficients + intercept). Inference is a pure dot-product → sigmoid; needs neither
  sklearn nor pickle. Training needs sklearn (guarded; already a dep via the ml extra).
- **Per-deployment scope** ("global") for v1; per-object models are a later refinement.

HARD CONSTRAINTS: no changes under `app/detection/`, `app/profiling/`, `app/coverage/`,
`app/planning/`, `app/policy/`. No NEW dependencies (sklearn/numpy already available via the ml
extra; inference must work without them). All existing tests stay green; existing API keys unchanged
(new keys/endpoints only).

## 1. Feature engineering — `app/learning/__init__.py` (empty) + `app/learning/features.py`
- `FEATURE_VERSION = 1`.
- `KNOWN_FINDING_TYPES: tuple[str, ...]` = sorted canonical list of our detector finding types:
  required_missing, picklist_invalid, reference_missing, invalid_format, invalid_stage,
  missing_account, missing_required, missing_contract_closed_won, non_positive_amount,
  stale_open_opportunity, missing_close_date, duplicate_name, numeric_outlier, robust_outlier,
  rare_combination, pattern_break, ml_anomaly, orphan_reference, missing_children.
- `SEVERITY_ORD = {"high": 3.0, "medium": 2.0, "low": 1.0}`.
- `def finding_features(finding: dict) -> dict[str, float]`: build a flat numeric feature dict from a
  serialised finding (has keys: finding_type, severity, confidence, priority, field_name, evidence):
  - `sev_ord` (via SEVERITY_ORD, default 2.0), `confidence`, `priority`, `has_field` (1.0 if
    field_name else 0.0).
  - one-hot `type=<t>` for each t in KNOWN_FINDING_TYPES (1.0 if finding_type==t else 0.0; unknown
    type → all zeros — safe).
  - generic evidence numerics (0.0 when absent): `ev_anomaly_score` = abs(evidence.get("anomaly_score",0)),
    `ev_max_absz` = max(|c["z"]| for c in evidence.get("top_contributors", [])) or 0,
    `ev_modified_z` = abs(evidence.get("modified_z",0)), `ev_lift` = evidence.get("lift",0).
- `def feature_names() -> list[str]`: the deterministic ordered feature-name list (fixed columns:
  sev_ord, confidence, priority, has_field, then type=<t> for each known type, then the 4 ev_* names).
- `def vectorize(finding: dict) -> list[float]`: finding_features → list in feature_names() order.

## 2. Persistence — table + repo (`app/persistence/database.py`, `repositories.py`)
- `database.py setup()`: `CREATE TABLE IF NOT EXISTS learned_models (id TEXT PRIMARY KEY, scope TEXT
  NOT NULL, feature_version INTEGER NOT NULL, model_json TEXT NOT NULL, n_labels INTEGER NOT NULL,
  metrics_json TEXT NOT NULL, trained_at TEXT NOT NULL);`
- New `class LearnedModelRepository`:
  - `save(conn, *, scope, feature_version, model: dict, n_labels, metrics: dict) -> str` (uuid id,
    trained_at=now(); json.dumps model + metrics).
  - `get_latest(conn, scope="global") -> dict | None`: newest by trained_at,rowid; returns
    `{id, scope, feature_version, model, n_labels, metrics, trained_at}` (model/metrics parsed).
- `FindingRepository.labeled_findings(conn) -> list[dict]`: `SELECT f.*, fb.label, fb.created_at AS
  label_created_at FROM findings f JOIN feedback fb ON fb.finding_id = f.id WHERE fb.label IN
  ('real_issue','false_positive','valid_exception') ORDER BY fb.created_at ASC, fb.rowid ASC`. If a
  finding has multiple labels, keep the LATEST (dedupe by finding id keeping last). Return serialised
  finding dicts each with `label` and `label_created_at`; y = 1 if label=='real_issue' else 0.

## 3. Service — `app/services/learning_service.py` — `class LearningService`
`__init__(self, *, database, findings: FindingRepository, models: LearnedModelRepository,
min_labels: int = 40, min_per_class: int = 8, holdout_frac: float = 0.2, random_state: int = 42)`.
- `def train(self) -> dict`:
  - Fetch `labeled_findings`. Build y (accept=1). Counts: n, n_pos, n_neg.
  - COLD-START GUARD: if n < min_labels or n_pos < min_per_class or n_neg < min_per_class →
    return `{"trained": False, "reason": f"insufficient labels (n={n}, pos={n_pos}, neg={n_neg}; need
    >= {min_labels} total and >= {min_per_class} per class)", "n_labels": n}`. Do NOT save a model.
  - Guarded sklearn/numpy import; if unavailable → `{"trained": False, "reason": "sklearn unavailable"}`.
  - TIME-BASED split: rows already ordered oldest→newest by label_created_at; holdout = last
    `ceil(holdout_frac*n)` rows, train = the rest. (If holdout has only one class, still train but
    mark holdout metrics as null and note it.)
  - Standardize: compute per-feature mean/std on the TRAIN split (std==0 → 1.0). Fit
    `LogisticRegression(max_iter=1000, random_state=...)` on standardized train X, y.
  - Evaluate on holdout (standardized with TRAIN mean/std): accuracy; precision/recall for the
    positive class; roc_auc if both classes present (else null).
  - Build `model = {"feature_names": [...], "means": [...], "stds": [...], "coef": [...],
    "intercept": float, "feature_version": FEATURE_VERSION}`.
  - `models.save(scope="global", feature_version=FEATURE_VERSION, model=model, n_labels=n,
    metrics={accuracy, precision, recall, roc_auc, n_train, n_holdout})`.
  - Return `{"trained": True, "n_labels": n, "metrics": {...}}`.
- `def predict_acceptance(self, findings: list[dict]) -> dict[str, dict] | None`:
  - Load latest global model; if None or its feature_version != FEATURE_VERSION → return None.
  - PURE inference (no sklearn): for each finding, vectorize → standardize with stored means/stds →
    `z = sum(coef_i * xstd_i) + intercept` → `p = 1/(1+exp(-z))`. Also compute top-3 contributions
    `coef_i * xstd_i` by |value| mapped back to feature_names.
  - Return `{finding_id: {"p_accept": round(p,4), "top_factors": [{"feature","contribution"}...]}}`.
  - Uses only `math`/pure python (may use numpy if present but must not require it).
- `def status(self) -> dict`: latest model summary (or none) + current label counts +
  `ready` boolean (whether thresholds are met).

## 4. Wire into ranking — `app/services/ranking_service.py`
- `RankingService.__init__` gains optional `learner: "LearningService | None" = None` (avoid import
  cycle: type as string / TYPE_CHECKING; injected at container).
- In `rank(self, findings)`: if `self.learner` is not None, call
  `preds = self.learner.predict_acceptance(findings)`. If `preds` is not None (a model exists):
  for each finding set `rank_score = round(clamp(priority + weight*(preds[id]["p_accept"] - 0.5)),4)`,
  add `model_p_accept` and `model_factors` keys, and `ranking_basis = "model"`. If `preds` is None →
  EXACTLY today's heuristic path (unchanged), with `ranking_basis = "heuristic"` added. Re-sort by
  rank_score desc as today. Original keys untouched.
- Container: build `LearnedModelRepository` + `LearningService`; pass `learner=learning_service` into
  `RankingService`; expose `learning_service` on Container. (RankingService is built before
  LearningService today — reorder so LearningService is built first, or set `ranking_service.learner`
  after both exist. Keep it clean.)

## 5. API — `app/api/routes_learning.py` (NEW; register in main.py)
- `POST /api/learning/train` → `container.learning_service.train()` (returns the dict; always 200).
- `GET /api/learning/status` → `container.learning_service.status()`.
- Add both to README API list.

## 6. Tests — `tests/test_learning.py` (NEW)
Helper: insert N synthetic findings via FindingRepository.upsert_many (fabricated Candidates) then a
feedback row per finding via FeedbackRepository.insert, controlling the accept/reject label.
1. **Learns signal**: 50 `required_missing` findings labeled real_issue + 50 `ml_anomaly` labeled
   false_positive (+ vary confidence a bit). `train()` → trained True, holdout accuracy >= 0.8. Then
   `predict_acceptance` on one fresh required_missing vs one ml_anomaly → P(accept) higher for
   required_missing.
2. **Cold-start guard**: only 12 labels → `train()` trained False, reason mentions "insufficient";
   `predict_acceptance([...])` returns None; no row in learned_models.
3. **Ranking uses model then falls back**: after (1) trains a model, `RankingService(learner=...)`
   `.rank([required_missing, ml_anomaly with equal priority])` puts required_missing first and sets
   `ranking_basis == "model"`. With a FRESH deployment (no model), rank sets
   `ranking_basis == "heuristic"` and behaves exactly as before (existing ranking test still passes).
4. **Pure inference without sklearn**: train a model; then monkeypatch so importing sklearn raises in
   the predict path; assert `predict_acceptance` STILL returns probabilities (pure math from stored
   JSON).
5. **Explainability**: a prediction's `top_factors` is a non-empty list of {feature, contribution}
   and includes the finding's `type=<...>` feature for a strongly-typed example.
6. **API**: `/api/learning/status` shows ready False + label counts on a fresh db; after enough
   labels + `/api/learning/train`, status shows a model with metrics.

## 7. Docs
- `docs/universal-strategy.md` §7: "Supervised model trained on labels" row → the training pipeline
  now EXISTS (cold-start-guarded, heuristic fallback); still awaiting real label volume to activate in
  production.
- README: learning endpoints + one line under the trust loop.

## Done criteria
`python -m pytest -q` + healthcheck green; all existing tests unmodified. Report files changed, both
test tails, and CONFIRM: (a) model learns the synthetic signal (holdout acc + required_missing >
ml_anomaly P(accept)); (b) cold-start guard blocks training and predict returns None; (c) ranking uses
model when present and falls back to heuristic when absent; (d) inference works without sklearn;
(e) full-suite pass count.
