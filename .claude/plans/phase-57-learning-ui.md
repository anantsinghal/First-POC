# Phase 57 — Surface the learning loop in the dashboard UI (closes the flywheel)

FRONTEND ONLY: edit app/static/index.html, app/static/app.js, app/static/styles.css. NO Python
changes (the backend endpoints GET /api/learning/status, POST /api/learning/train already exist, and
RankingService already adds per-finding model keys). Keep every existing flow working; keep the full
pytest suite green (frontend edits shouldn't affect it — verify anyway).

FIRST: read the EXACT response shapes you must consume, and use their exact key names:
- app/services/learning_service.py -> `status()` (what keys it returns: model presence, label counts,
  ready flag, thresholds, latest metrics/trained_at).
- app/services/ranking_service.py -> `rank()` (the per-finding keys it adds: ranking_basis, and when a
  model is active, model_p_accept + model_factors; confirm exact names).
Do not invent keys — use whatever those two functions actually emit.

## 1. Learning panel — index.html + app.js
- index.html: add near the metrics panel:
  `<section class="panel"><div class="panel-title"><h2>Learned ranking</h2><button id="trainModel" type="button">Train model</button></div><div id="learning"><p>Loading…</p></div></section>`
- app.js `showLearning(status)`:
  - If NO model active: render "Ranking mode: heuristic (learned model not active yet)." + a labels
    progress line using the real counts from status (e.g. "Labels: X — need ≥ threshold total and ≥
    per-class before training"). If status exposes readiness, show "Ready to train: yes/no".
  - If a model IS active: render "Ranking mode: LEARNED MODEL — trained on N labels on <date>." plus
    any holdout metrics status exposes (e.g. accuracy) shown as a percentage when present (guard null).
  - Keep it compact chips/lines consistent with showMetrics/showQuality style.
- app.js: in `refresh()`, `try { showLearning(await api('/api/learning/status')) } catch { ... clear }`.
- `#trainModel` click handler: setMessage('Training…'); POST /api/learning/train; on response show a
  concise outcome via setMessage (trained true -> "Model trained on N labels (acc …)"; trained false
  -> the returned reason); then `await refresh()`. Reuse the existing error pattern.

## 2. Finding cards show the model's say — app.js showFindings
- When a finding has `ranking_basis === 'model'` and its model-probability key is a number, append a
  small `.model-chip` to the finding-head after the existing rank-tag: text like
  `model: NN% real` (NN = round(p*100)). Add a `title=` attribute listing the top model_factors
  (feature + sign) so hovering explains WHY. When ranking_basis !== 'model', add nothing new.
- Do NOT remove the existing rank-tag logic from phase 52.

## 3. CSS — styles.css
`.model-chip { font-size:.66rem; color:var(--good); background:#0e2a1c; border:1px solid var(--line); padding:2px 6px; border-radius:999px; white-space:nowrap; }`
`#trainModel { font-size:.72rem; padding:6px 10px; }`
`.learning-line { font-size:.82rem; color:var(--muted); }` `.learning-line strong { color:var(--ink); }`

## 4. Self-check
Run `python -m pytest -q` (must stay green — report the tail). Grep the three static files and report
presence of: `#learning`, `#trainModel`, `showLearning`, `model-chip`. Do NOT do a live browser run —
Claude will verify that in the preview.

## Done criteria
Full suite green; hooks present; existing flows untouched. CONFIRM: (a) exact status() keys you
consumed; (b) exact rank() per-finding model keys you consumed; (c) full-suite pass count.
