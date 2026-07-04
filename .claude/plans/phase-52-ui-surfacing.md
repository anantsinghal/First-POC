# Phase 52 — UI surfacing: database scans, drift panel, ranking display (FINAL build phase)

Frontend ONLY: edit `app/static/index.html`, `app/static/app.js`, `app/static/styles.css`. No Python.
Keep `ml-tutorial.html` untouched and all existing flows (demo/upload/multi/feedback) working.
`python -m pytest -q` must stay green (frontend changes shouldn't affect it — verify anyway).

## 1. Database scan form — `index.html` + `app.js`
Add a section after the Multi-object upload:
`<section class="upload"><strong>Database scan (SQLite)</strong><form id="dbUpload">
<label>Table <input name="table_name" value="" placeholder="orders" required></label>
<label>.db file <input name="file" type="file" accept=".db,.sqlite,.sqlite3" required></label>
<label class="chk"><input type="checkbox" name="stream"> stream (bounded memory)</label>
<button>Scan database</button></form>
<small>The same engine reads the table's schema (NOT NULL, CHECK enums, foreign keys) as its
contract.</small></section>`
`app.js`: submit handler mirroring `#upload` — POST the file (FormData key `file`) to
`/api/scans/database?table_name=<enc>` or `/api/scans/database/stream?table_name=<enc>` when the
stream checkbox is checked; setMessage + refresh; error path like the others.

## 2. Drift panel — `index.html` + `app.js`
Section near quality: `<section class="panel"><h2>Drift vs previous scan</h2><div id="drift"><p>Run
two scans of the same object to compare.</p></div></section>`.
`app.js` in `refresh()`: when a latest scan exists, `GET /api/scans/${scan.id}/drift` (try/catch →
clear on error). Render: if `drift_count == 0` → `<p>No drift detected${d.baseline_scan_id ? ' vs
baseline' : ' (no previous scan of this object)'}.</p>`; else one `.drift-row` per drift:
`<strong>{kind}</strong> {field} · {why}` (truncate why ~120 chars).

## 3. Ranking display — `app.js` (+ tiny CSS)
- Finding cards: when `item.rank_score !== undefined && item.rank_score !== item.priority`, append a
  small `.rank-tag` next to the score: `rank {(item.rank_score).toFixed(2)}` with a ▼/▲ vs priority.
- Metrics panel: `showMetrics` — if `m.type_acceptance` has keys, append chips
  `Acc[{humanizeType(t)}]: {Math.round(a.acceptance*100)}% ({a.labels})` for each entry (cap 6).

## 4. CSS
`.chk { display:flex; flex-direction:row; align-items:center; gap:6px; }`
`.drift-row { font-size:.82rem; color:var(--muted); border-bottom:1px solid var(--line);
padding:4px 0; }` `.drift-row strong { color: var(--warn); font-size:.75rem; text-transform:
uppercase; letter-spacing:.05em; }`
`.rank-tag { font-size:.68rem; color:var(--warn); white-space:nowrap; }`

## 5. Self-check (report, don't screenshot)
`python -m pytest -q` green; grep the three static files for `#dbUpload`, `#drift`, `rank-tag`,
`type_acceptance` hooks and report presence. Claude will verify live in the preview.

## Done criteria
Tests green; hooks present; existing flows untouched. List files changed.
