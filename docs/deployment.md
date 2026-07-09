# Deployment runbook

> **STATUS: WRITTEN BUT NOT YET VERIFIED.** Docker could not run on the development machine
> (Docker Desktop failed to start), so this image has never been built. Before first production
> use, run the verification steps in  section 5 on a
> Docker-capable host: build, run with SCANNER_API_KEY, confirm /api/health -> 200, the 401/200
> API-key matrix, and container health reaching healthy.


Run the scanner on your own servers with Docker. Data stays in a named volume under `/data`;
secrets stay in the environment (never bake them into the image).

## Build & run

```powershell
# Compose (recommended)
docker compose up -d --build
# UI: http://127.0.0.1:8000

# Or plain Docker
docker build -t scanner:local .
docker run -d --name scanner -p 8000:8000 `
  -e SCANNER_API_KEY=change-me `
  -v scanner-data:/data `
  scanner:local
```

**First-run smoke**

1. `GET /api/health` → `200` (no API key required).
2. `POST /api/scans/demo` with header `X-API-Key: <your key>` → `200` and a non-zero
   `finding_count` (demo CSV ships in the image).
3. Open `http://127.0.0.1:8000` — dashboard; enter the key once if prompted.

```powershell
curl http://127.0.0.1:8000/api/health
curl -X POST http://127.0.0.1:8000/api/scans/demo -H "X-API-Key: change-me"
```

## Security

- **Always set `SCANNER_API_KEY` in production.** When set, every data-bearing `/api/*` route
  requires a matching `X-API-Key` header.
- Clients pass the key as `X-API-Key: <secret>`. The browser UI stores it in `localStorage`
  as `scanner_api_key` after a 401 prompt.
- **Stays open without a key:** `GET /api/health` (liveness), and the static UI shell
  (`/`, `/static/*`, `/tutorial`). Do not expose the port publicly without the API key
  (and preferably a reverse proxy / network control).

## Data & backups

Everything durable lives in the **`/data` volume** (env `SCANNER_DATA_DIR=/data`):

| Path | Contents |
|------|----------|
| `/data/scanner.db` | SQLite: scans, findings, feedback, audit, catalog |

**Backup:** copy `scanner.db` out of the volume (e.g. `docker compose cp scanner:/data/scanner.db ./backup/`).
**Restore:** stop the container, replace `scanner.db` in the volume, start again.

Demo CSVs under `/app/data` are image content, not runtime state.

## Retention

- **On-demand:** `POST /api/admin/purge` (requires API key) — see API schemas for age/count options.
- **Scheduled:** on the host, point cron or Windows Task Scheduler at
  `python -m tools.scheduled_scan …` with `SCANNER_DATA_DIR` set to the same volume path
  (or run purge via `curl` against the running container). See [`scheduling.md`](scheduling.md).

## Upgrades

```powershell
docker compose build --pull
docker compose up -d
```

Scan/finding **fingerprints are stable across versions** — re-scans upsert by fingerprint;
reviewer feedback is preserved. Prefer backing up `scanner.db` before major upgrades.

## Salesforce connectivity env vars

Pass through compose / `docker run -e` (never commit):

| Variable | Use |
|----------|-----|
| `SF_LOGIN_URL` + `SF_CLIENT_ID` + `SF_CLIENT_SECRET` | Production client-credentials / External Client App |
| `SF_INSTANCE_URL` + `SF_ACCESS_TOKEN` | Quick token-based access (e.g. from `sf org display`) |
| `SF_API_VERSION` | Optional API version override |

Verify with `POST /api/salesforce/verify`, then scan via `/api/scans/salesforce*`.
Details: [`salesforce-connection.md`](salesforce-connection.md).
