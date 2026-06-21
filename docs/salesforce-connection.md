# Salesforce sandbox connection hand-off

The POC uses a direct `httpx` connector: it does not call `sf`, `sfdx`, or any CLI. It should be enabled only for a sandbox or developer org at first.

1. Create a Salesforce Connected App with OAuth scopes limited to API access needed by the integration user. Use a dedicated, least-privilege integration user and a dedicated permission set.
2. Record approved objects and fields in a versioned field policy. Do not collect raw PII merely because a field is readable.
3. Discover permissions and object/field metadata first. Persist field-level-security and queryability outcomes in the scan coverage report.
4. Configure `.env` with a pre-issued `SF_INSTANCE_URL` + `SF_ACCESS_TOKEN` for a short POC session, or set `SF_LOGIN_URL`, `SF_CLIENT_ID`, and `SF_CLIENT_SECRET` for OAuth client credentials. Verify direct API access with `POST /api/salesforce/verify`; it calls REST Describe, Tooling API, and Metadata SOAP API.
5. The POC's `POST /api/scans/salesforce` uses direct REST Describe and SOQL for a bounded scan. Production extraction should use Bulk API 2.0 query jobs. Before extraction create a scan manifest with API version, SOQL, selected fields, policy version, integration-user identity, closed time window, and a job/chunk checksum ledger.
6. Use overlapping `SystemModstamp` windows and canonical 18-character IDs; reconcile periodically with a full scan. Treat deletes, merges, reparenting, and schema changes as invalidation events.
7. Store OAuth secrets in a real secret manager. Never place them in this app's SQLite database or log them.

The implementation must report skipped/inaccessible fields and objects. Missing access is a coverage failure, never evidence that a Salesforce value is blank.
