# Phase 12 — Surface finding types in the dashboard UI

Findings already render generically, so the new types (robust_outlier, stale_open_opportunity,
duplicate_name, missing_close_date) already appear. This phase makes them legible: severity-colored
badges, a humanized finding-type chip per card, and a by-type breakdown above the review queue.
Frontend only — no Python, no API changes. Touch only `app/static/index.html`,
`app/static/styles.css`, `app/static/app.js`. Keep `ml-tutorial.html` unchanged (a test reads it).

## `app/static/index.html`
- In `#findingTemplate`'s `.finding-head`, add a type chip element after the `<h3></h3>`:
  `<span class="type-tag"></span>` (so head = badge, title, type-tag, score).
- In the review-queue panel title row (the `<div class="panel-title">` containing `<h2>Review
  queue</h2>` and `<span id="findingCount"></span>`), add a breakdown container right after it,
  inside the same panel: `<div id="findingTypes" class="type-breakdown"></div>`.

## `app/static/app.js`
- Add a label map + humanizer near the top:
  ```
  const TYPE_LABELS = {
    missing_required:'Missing required field', invalid_stage:'Invalid stage',
    missing_account:'Missing account', missing_contract_closed_won:'Closed Won · no contract',
    non_positive_amount:'Non-positive amount', numeric_outlier:'IQR outlier',
    robust_outlier:'Robust (MAD) outlier', rare_combination:'Rare combination',
    stale_open_opportunity:'Stale open opportunity', duplicate_name:'Possible duplicate',
    missing_close_date:'Missing close date',
  };
  const humanizeType = (t) => TYPE_LABELS[t] || t.replace(/_/g,' ').replace(/\b\w/g, c=>c.toUpperCase());
  ```
- In `showFindings`:
  - Set the severity class on the badge so CSS can color it:
    `const badge = $('.badge', node); badge.textContent = item.severity; badge.classList.add(item.severity);`
  - Populate the type chip: `$('.type-tag', node).textContent = humanizeType(item.finding_type);`
  - After computing `findings`, render the breakdown into `#findingTypes`: count by
    `finding_type`, and set its innerHTML to a chip per type sorted by count desc, e.g.
    `<span class="type-chip">${humanizeType(type)} ×${count}</span>`. When there are no findings,
    clear it (`''`).

## `app/static/styles.css`
- Replace the single `.badge` background with a neutral default and add severity variants using the
  existing palette vars:
  - `.badge.high { background:#4c1d2d; color:#fecdd3; }` (red — keep current look for high)
  - `.badge.medium { background:#4a3410; color:#fde7b0; }` (amber)
  - `.badge.low { background:#21304d; color:#bcd0f0; }` (muted blue)
  - (keep the base `.badge` sizing/shape rules; just move color into the severity classes, with a
    sensible default background for safety.)
- `.type-tag { font-size:.66rem; font-weight:800; letter-spacing:.04em; color:var(--blue);
  background:#0d1c33; border:1px solid var(--line); padding:3px 7px; border-radius:999px;
  white-space:nowrap; }`
- `.type-breakdown { display:flex; flex-wrap:wrap; gap:6px; margin-top:10px; }`
- `.type-chip { font-size:.72rem; color:var(--muted); background:#101c31; border:1px solid
  var(--line); padding:3px 8px; border-radius:999px; }`
- Make `.finding-head` wrap gracefully (it already uses flex with justify-between); ensure the
  type-tag sits with the title — set `.finding-head { flex-wrap:wrap; }` and let `h3` take available
  space (`h3 { flex:1 1 auto; }` is fine if it doesn't break other layout — verify the header still
  reads badge · title · type · score).

## Verification (do this; report what you see)
- `python -m pytest -q` still green (UI changes don't touch tests, but confirm).
- Confirm the three static files contain the new hooks: `type-tag`, `findingTypes`, `TYPE_LABELS`,
  `.badge.medium`.

## Constraints
- Frontend only; no API/JSON changes. Don't break existing demo/upload/feedback JS flows.

## Done criteria
- Tests green; new hooks present. Report files changed.
