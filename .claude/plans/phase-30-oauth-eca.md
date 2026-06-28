# Phase 30 — Enterprise OAuth provision (External Client App / Connected App client-credentials)

The consumer-key/secret path already exists in `session_from_environment()` (client-credentials flow).
This phase makes it **mock-testable, verified, and explicit** so it's the production auth mechanism (the
CLI access-token stays as the quick-test alternative). No behavior change to the existing flow.

## 1. `app/salesforce/session.py` — make the token exchange testable
- Add an optional `transport: httpx.AsyncBaseTransport | None = None` parameter to
  `session_from_environment(...)`. Pass it through to the `httpx.AsyncClient(...)` used for the token
  POST: `httpx.AsyncClient(timeout=30, transport=transport)`. Default `None` = unchanged production
  behavior.
- Keep the exact existing logic/precedence:
  1. If `SF_INSTANCE_URL` + `SF_ACCESS_TOKEN` are set → return that session (no token call).
  2. Else require `SF_CLIENT_ID` + `SF_CLIENT_SECRET` (raise `SalesforceError(None, ...)` if missing) and
     POST `grant_type=client_credentials` to `{SF_LOGIN_URL}/services/oauth2/token`; on HTTP error raise
     `SalesforceError`; if the response lacks `access_token`/`instance_url` raise `SalesforceError(502,...)`;
     else return `SalesforceSession(instance_url, access_token, api_version)`.
- Add/extend the docstring to note: client-credentials requires `SF_LOGIN_URL` to be the org's **My
  Domain** URL, and works for both Connected Apps and External Client Apps configured for the flow with a
  Run-As user.

## 2. Tests — `tests/test_oauth.py` (NEW)
Use `monkeypatch` to set/clear env vars and `httpx.MockTransport` for the token endpoint:
- **Client-credentials success**: set `SF_CLIENT_ID`/`SF_CLIENT_SECRET`/`SF_LOGIN_URL=
  https://carhuborg.my.salesforce.com`, clear `SF_INSTANCE_URL`/`SF_ACCESS_TOKEN`. MockTransport handler
  asserts the request path ends with `/services/oauth2/token` and the form body contains
  `grant_type=client_credentials` + the client id/secret; it returns
  `{"access_token":"TKN","instance_url":"https://carhuborg.my.salesforce.com"}`. Assert
  `await session_from_environment(transport=mock)` returns a `SalesforceSession` with that instance_url +
  token + the configured api_version. (Use `asyncio.run`.)
- **Access-token precedence**: set `SF_INSTANCE_URL`+`SF_ACCESS_TOKEN`; assert the returned session uses
  them and the MockTransport is NEVER called (e.g. handler raises if invoked).
- **Not configured**: clear all four → `pytest.raises(SalesforceError)`.
- **OAuth HTTP error**: MockTransport returns 400 → `pytest.raises(SalesforceError)`.
- Do NOT modify other tests.

## Constraints
- `from __future__ import annotations`; type hints; docstrings. Production call sites
  (`verify_direct_api_access`, the scan route) keep calling `session_from_environment()` with no
  transport — unchanged.

## Done criteria
- `python -m pytest -q` green. Report files + results; confirm the consumer-key/secret (client-
  credentials) flow is exercised and returns a valid session via the mock.
