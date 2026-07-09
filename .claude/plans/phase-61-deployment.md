# Phase 61 — Deployment packaging (Docker + runbook) — FINAL buildable phase

## Why
"Runs inside the enterprise's own servers" is currently a sentence, not an artifact. Docker 29.5.3 is
available locally, so this is fully verifiable.

HARD CONSTRAINTS: zero changes under `app/` and zero changes to existing tests. New files only
(plus README + .dockerignore). No new Python deps.

## 1. `Dockerfile` (repo root)
- `FROM python:3.12-slim`.
- Create non-root user `scanner` (uid 10001); `WORKDIR /app`.
- Copy `pyproject.toml` + `README.md` first, then `app/ data/ docs/` (layer caching); RUN
  `pip install --no-cache-dir .[ml,scale]` (CHECK the extras names in pyproject.toml first — ml and
  scale exist; dev NOT installed).
- ENV `SCANNER_DATA_DIR=/data`; `VOLUME /data`; `EXPOSE 8000`; `USER scanner`.
- `HEALTHCHECK --interval=30s --timeout=5s --retries=3 CMD python -c "import urllib.request;
  urllib.request.urlopen('http://127.0.0.1:8000/api/health')"` (stdlib, no curl in slim).
- `CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]`.
- NOTE: uvicorn must be a declared dependency — CHECK pyproject [project] dependencies; if uvicorn is
  missing there (dev-only), add it to core dependencies in pyproject.toml (that is ALLOWED — it's
  packaging, not app code).

## 2. `.dockerignore`
`.git`, `.venv`, `__pycache__`, `*.pyc`, `tests/`, `.claude/`, `data/scanner.db`, `*.log`, `.env*`
(BUT keep `data/demo_opportunities.csv` — needed by the demo scan; verify the ignore rules don't
exclude it).

## 3. `docker-compose.yml`
Service `scanner`: `build: .`, `ports: ["8000:8000"]`, named volume `scanner-data:/data`,
`env_file: .env` marked optional if compose supports it else document, environment passthrough
for `SCANNER_API_KEY`, `SF_INSTANCE_URL`, `SF_ACCESS_TOKEN`, `SF_LOGIN_URL`, `SF_CLIENT_ID`,
`SF_CLIENT_SECRET`, `SF_API_VERSION`; `restart: unless-stopped`. Volumes section defines
`scanner-data`.

## 4. `docs/deployment.md` — ops runbook (concise, ~1 page)
Sections: Build & run (compose up; first-run smoke: GET /api/health, POST /api/scans/demo with the
API key header); Security (ALWAYS set SCANNER_API_KEY in prod; how clients pass X-API-Key; what
stays open); Data & backups (everything lives in the /data volume: scanner.db — back it up by
copying; restore = replace file); Retention (POST /api/admin/purge or schedule tools/scheduled_scan
via host cron/Task Scheduler pointing SCANNER_DATA_DIR at the volume); Upgrades (docker compose
build --pull && up -d; scans/finding fingerprints are stable across versions); Salesforce
connectivity env vars.

## 5. VERIFY with real Docker (this machine has Docker 29.5.3)
1. `docker build -t scanner:phase61 .` — must succeed.
2. `docker run -d --name scanner-verify -p 18000:8000 -e SCANNER_API_KEY=verify123 scanner:phase61`
3. Poll `http://127.0.0.1:18000/api/health` until 200 (no key needed — exempt).
4. `POST /api/scans/demo` WITHOUT key → expect 401; WITH `X-API-Key: verify123` → 200 and
   finding_count == 10.
5. `docker inspect --format '{{.State.Health.Status}}' scanner-verify` eventually `healthy`.
6. Cleanup: `docker rm -f scanner-verify` (keep the image).
Use python/curl from the host for the HTTP checks; capture the four results for the report.

## 6. Docs
README Run section: add a Docker quick-start block (3 lines: compose up, open :8000, set
SCANNER_API_KEY note) linking docs/deployment.md.

## Done criteria
`python -m pytest -q` unchanged-green (packaging shouldn't affect it — run to prove). Docker verify
steps 1–5 pass. END REPORT CONFIRM: (a) build result + image size; (b) in-container demo scan 401/200
matrix result; (c) health status reached 'healthy'; (d) whether pyproject needed the uvicorn dep
added; (e) full-suite count.
