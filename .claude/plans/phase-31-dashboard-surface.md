# Phase 31 — Surface new capabilities in the dashboard UI

Make the backend capabilities visible/usable: friendly labels for the newer finding types, an **audit
log** panel, an **org catalog (schema)** view, and a **multi-object scan** upload form. Frontend ONLY —
edit `app/static/index.html`, `app/static/app.js`, `app/static/styles.css`. No Python/API changes.
Keep `ml-tutorial.html` and all existing flows working.

## 1. `app/static/app.js`
- Extend `TYPE_LABELS` with the newer finding types (the rest already fall back to humanize, but add
  these for clarity): `picklist_invalid:'Invalid picklist value'`, `reference_missing:'Missing required
  reference'`, `required_missing:'Required field empty'`, `invalid_format:'Invalid format'`,
  `orphan_reference:'Orphan reference'`, `missing_children:'Missing related records'`.
- **Audit panel**: in `refresh()`, after loading the dashboard, `fetch('/api/audit?limit=20')` and render
  into `#audit`: for each entry a row showing `ts` (localized), `event`, and a compact `detail` summary
  (e.g. JSON of detail, or key fields). On empty → "No audit entries yet."
- **Org catalog**: in `refresh()`, read `scan.schema` from the latest scan detail (already in the
  dashboard's `latest_scan`). If present, render into `#catalog`: object name + a list of its fields as
  small rows `name · data_type[· required][· picklist:N][· ref→Targets]`. If absent → clear `#catalog`
  (CSV scans have no schema).
- **Multi-object scan**: wire `#multiUpload` submit → read the `primary_object` input + the `multiple`
  file input; build `FormData` appending each file under key `files`; POST to
  `/api/scans/multi?primary_object=<encoded>`; on success `setMessage(...)` + `refresh()`; on error
  `setMessage(err, true)`. (Mirror the existing single `#upload` handler's error handling.)

## 2. `app/static/index.html`
- Add a **Multi-object scan** section (after the existing single-CSV upload section):
  `<section class="upload"><strong>Multi-object scan (relationships)</strong><form id="multiUpload">
  <label>Primary object <input name="primary_object" value="Account" required></label>
  <label>CSV files (named &lt;Object&gt;.csv) <input name="files" type="file" accept=".csv" multiple
  required></label><button>Upload & scan</button></form><small>Filenames become object API names; the
  primary plus its related objects enable orphan & missing-children checks.</small></section>`
- Add an **Org catalog** section: `<section class="panel"><h2>Org catalog (discovered schema)</h2>
  <div id="catalog"><p>Run a Salesforce scan to populate the schema.</p></div></section>`
- Add an **Audit log** section: `<section class="panel"><h2>Audit log</h2><div id="audit"></div></section>`
- Place these sensibly (catalog + audit near the bottom, multi-object near the existing upload).

## 3. `app/static/styles.css`
Add light styling reusing existing variables: `#catalog`/`#audit` rows compact and muted
(`.catalog-row`, `.audit-row` with small font, `var(--muted)`, bottom border `var(--line)`), consistent
with the existing `.scan` row style. No layout regressions to the existing panels.

## Verification (Claude will do this)
Start the preview server, run a demo scan via the UI, and confirm via DOM: the audit panel shows a
`scan_completed` entry, the new finding labels render, and the multi-object form exists. (Catalog stays
empty for CSV/demo since there's no schema — expected.)

## Constraints
- Frontend only; do not change Python, API, or `ml-tutorial.html`. Don't break demo/upload/feedback JS.
- `python -m pytest -q` must stay green (it won't be affected).

## Done criteria
- Tests green; the three new sections render; audit panel populates after a scan; multi-object form
  posts to `/api/scans/multi`. Report files changed.
