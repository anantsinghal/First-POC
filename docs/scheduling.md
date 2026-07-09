# Scheduling scans (Windows Task Scheduler + cron)

The CLI entrypoint is:

```text
python -m tools.scheduled_scan {org|object|table} [flags]
```

Each run prints a **single final status line** suitable for log grepping:

```text
SCHEDULED_SCAN status=ok|error mode=<mode> findings=<n>
```

Exit code **0** on success, **1** on any error. Scan audit events are written by the
services themselves — the CLI does not duplicate them.

## Modes

| Subcommand | What it does |
|---|---|
| `org` | Org-wide discover → size → route via `orchestrator_service.scan_org` (needs SF env creds) |
| `object --name Account` | Single Salesforce object via `SalesforceObjectSource`; add `--incremental` for watermarked delta |
| `table --db path --table name` | Local SQLite table; add `--stream` for bounded-memory `stream_scan` |

### Useful flags

- `org`: `--max-objects N`, `--stream-threshold N`, `--api-budget N`
- `object`: `--name <ApiName>`, `--max-records N`, `--incremental`
- `table`: `--db <path>`, `--table <name>`, `--stream`

## Environment checklist

| Variable | Required for | Notes |
|---|---|---|
| `SCANNER_DATA_DIR` | all modes | Catalog / findings SQLite home (default `./data`) |
| `SF_LOGIN_URL` + `SF_CLIENT_ID` + `SF_CLIENT_SECRET` | `org`, `object` | Production client-credentials / ECA |
| *or* `SF_INSTANCE_URL` + `SF_ACCESS_TOKEN` | `org`, `object` | Quick token-based auth |
| `SCANNER_API_KEY` | **not needed** | CLI talks to services in-process; it bypasses HTTP — no API key |

Optional scale extras for large `table --stream` / org bulk routes: install
`pip install -e ".[scale]"` (duckdb + pyarrow).

## Windows Task Scheduler example

Daily org scan at 02:00 (adjust the Python path and working directory):

```powershell
schtasks /create /sc daily /st 02:00 /tn ScannerOrgScan /tr "C:\path\to\.venv\Scripts\python.exe -m tools.scheduled_scan org" /ru SYSTEM
```

Or with an explicit data dir:

```powershell
schtasks /create /sc daily /st 02:00 /tn ScannerOrgScan /tr "cmd /c set SCANNER_DATA_DIR=C:\scanner\data&& C:\path\to\.venv\Scripts\python.exe -m tools.scheduled_scan org" /ru SYSTEM
```

Table stream example (no Salesforce creds):

```powershell
schtasks /create /sc daily /st 03:00 /tn ScannerTableScan /tr "C:\path\to\.venv\Scripts\python.exe -m tools.scheduled_scan table --db C:\data\app.db --table deals --stream"
```

After creation, confirm with `schtasks /query /tn ScannerOrgScan /v /fo LIST`.

## cron example (Linux / macOS)

```cron
# Daily org scan at 02:15
15 2 * * * cd /opt/scanner && SCANNER_DATA_DIR=/var/lib/scanner/data /opt/scanner/.venv/bin/python -m tools.scheduled_scan org >> /var/log/scanner/org.log 2>&1

# Hourly single-object incremental
0 * * * * cd /opt/scanner && SCANNER_DATA_DIR=/var/lib/scanner/data /opt/scanner/.venv/bin/python -m tools.scheduled_scan object --name Account --incremental >> /var/log/scanner/account.log 2>&1

# Nightly local SQLite stream
30 1 * * * cd /opt/scanner && SCANNER_DATA_DIR=/var/lib/scanner/data /opt/scanner/.venv/bin/python -m tools.scheduled_scan table --db /data/app.db --table deals --stream >> /var/log/scanner/table.log 2>&1
```

Grep success:

```bash
grep 'SCHEDULED_SCAN status=ok' /var/log/scanner/*.log
```
