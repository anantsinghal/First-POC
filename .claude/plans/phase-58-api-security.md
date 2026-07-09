# Phase 58 — API security hardening (close the enterprise-grade hole)

## Why
The app's own API currently has ZERO authentication (verified 2026-07-05: no auth code anywhere in
app/api/ or main.py). Findings contain real field values; `/api/admin/purge` destroys data. For an
"enterprise-grade, in-boundary" product this must be closed — while keeping local dev friction-free.

HARD CONSTRAINTS: zero changes under `app/detection/`, `app/profiling/`, `app/coverage/`,
`app/planning/`, `app/policy/`. No new dependencies. All existing tests pass UNMODIFIED (auth is OFF
by default, so nothing existing changes behavior). New keys/behaviors only when the env var is set.

## 1. Auth dependency — `app/api/security.py` (NEW)
- `def api_key_guard(request: Request) -> None`: read `expected = os.environ.get("SCANNER_API_KEY")`.
  If not set → return (auth disabled). If set → compare `request.headers.get("X-API-Key")` using
  `hmac.compare_digest` (constant-time; guard None → mismatch). On mismatch raise
  `HTTPException(401, "Missing or invalid API key")`.
- EXEMPT paths: `/api/health` only (liveness must stay open). Implement by checking
  `request.url.path` at the top of the guard, OR (cleaner) by not applying the dependency to the
  health route — choose ONE, document it.
- Wire: in `app/main.py` `create_app()`, add the guard as a global dependency on each included router
  EXCEPT the system/health router — simplest correct approach:
  `app.include_router(r, dependencies=[Depends(api_key_guard)])` for scans/findings/salesforce/
  dashboard/audit/admin/metrics/learning routers; include the system router (which serves
  `/api/health`) WITHOUT the dependency. Static files (`/static`, `/`) stay open (the dashboard shell
  is not sensitive; its DATA calls are what's protected).
  CHECK `app/api/routes_system.py` first: if it contains more than /api/health (e.g. `/` and
  `/tutorial` HTML), that's fine — those are UI shell pages, leave them open; only ensure no
  data-bearing endpoint rides on the unprotected router (list what's on it in the report).

## 2. Security headers — `app/main.py`
`@app.middleware("http")` adding to every response:
`X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY`, `Referrer-Policy: no-referrer`,
`Cache-Control: no-store` for paths starting `/api/`. (No CSP — the inline-script-free static app
doesn't need one yet; do not add one, it risks breaking the dashboard.)

## 3. Dashboard key support — `app/static/app.js` (+ tiny index.html/styles.css)
- `api()` helper: attach `"X-API-Key": localStorage.getItem("scanner_api_key")` header when non-empty.
- On a 401 from `api()`: show a small inline bar (id `#authBar`, hidden by default, placed right
  under the header in index.html): a password-type input + Save button; Save writes
  localStorage.scanner_api_key, hides the bar, and calls `refresh()`. Also reveal the bar (instead of
  an error wall) when the initial `refresh()` fails with 401.
- Keep ALL existing flows working when no key is required (bar never appears).
- CSS: `.auth-bar { ... }` consistent with existing panels; keep minimal.

## 4. Tests — `tests/test_api_security.py` (NEW)
Use `tests.harness.api_client` + `monkeypatch.setenv` / `delenv` for `SCANNER_API_KEY` (the guard
reads the env PER REQUEST, so no app reload is needed for the key itself — assert that design works).
1. **Off by default**: without env, `/api/scans/demo` POST → 200; `/api/dashboard` → 200. (Existing
   suite already proves this broadly — one explicit assertion here.)
2. **On**: setenv KEY=`test-secret-123`: `/api/scans/demo` without header → 401; with wrong key →
   401; with correct header → 200. `/api/admin/purge` without header → 401. `/api/findings` without
   header → 401.
3. **Health stays open**: with env set, `/api/health` with NO header → 200.
4. **Static/UI shell open**: with env set, `GET /` → 200 without header.
5. **Constant-time compare**: assert `hmac.compare_digest` is used (import inspect of the module
   source or unit-call the guard) — a one-line sanity check, not timing measurement.

## 5. Docs
- README "Connect a Salesforce org" section: add a short "## Securing the scanner itself" block:
  set `SCANNER_API_KEY`, clients send `X-API-Key`, dashboard prompts once and stores locally,
  `/api/health` stays open for probes.
- `.env.example`: add commented `SCANNER_API_KEY=` with one-line explanation.

## Done criteria
`python -m pytest -q` green (existing tests unmodified) + new security tests pass. Report files
changed, test tail, and CONFIRM: (a) which routes ride the unprotected router; (b) 401 matrix result
(no header / wrong key / right key); (c) health + static open with key set; (d) full-suite count.
