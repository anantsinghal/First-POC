"""Optional API-key authentication for the scanner HTTP surface.

Auth is OFF by default: when SCANNER_API_KEY is unset, the guard is a no-op.
When set, clients must send matching X-API-Key. The env var is read per request
so tests (and ops) can flip the key without reloading the app.

Exemption of /api/health (and the UI shell pages) is done by NOT attaching this
dependency to the system router in main.create_app — not by path checks here.
"""

from __future__ import annotations

import hmac
import os

from fastapi import HTTPException, Request


def api_key_guard(request: Request) -> None:
    """Reject requests when SCANNER_API_KEY is set and X-API-Key does not match.

    On mismatch, sets request.state.auth_failed=True so middleware can append an
    auth_failed audit row (never logs key material). Audit write is NOT done here —
    no Database/AuditRepository is available in this dependency.
    """
    expected = os.environ.get("SCANNER_API_KEY")
    if not expected:
        return
    provided = request.headers.get("X-API-Key")
    if provided is None or not hmac.compare_digest(provided, expected):
        request.state.auth_failed = True
        raise HTTPException(status_code=401, detail="Missing or invalid API key")
