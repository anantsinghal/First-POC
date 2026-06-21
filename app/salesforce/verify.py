"""Direct API access verification (moved verbatim from monolithic salesforce.py)."""
from __future__ import annotations

import asyncio
from time import perf_counter
from typing import Any

from .client import SalesforceClient
from .session import session_from_environment, SalesforceError, SalesforceSession


async def verify_direct_api_access() -> dict[str, Any]:
    session = await session_from_environment()
    client = SalesforceClient(session)

    async def probe(plane: str, operation: str, call: Any, count: Any) -> dict[str, Any]:
        started = perf_counter()
        try:
            payload = await call()
            return {"plane": plane, "operation": operation, "status": "ok", "status_code": 200,
                    "record_count": count(payload), "duration_ms": int((perf_counter() - started) * 1000), "warning": None}
        except SalesforceError as exc:
            return {"plane": plane, "operation": operation, "status": "warning", "status_code": exc.status_code,
                    "record_count": None, "duration_ms": int((perf_counter() - started) * 1000), "warning": str(exc)}

    probes = await asyncio.gather(
        probe("REST API", "limits", client.limits, lambda payload: len(payload)),
        probe("Describe API", "global describe", client.describe_global, lambda payload: len(payload.get("sobjects") or [])),
        probe("Tooling API", "ApexClass query", lambda: client.tooling_query("SELECT Id, Name FROM ApexClass LIMIT 1"), lambda payload: len(payload.get("records") or [])),
        probe("Metadata SOAP API", "listMetadata CustomObject", client.metadata_custom_objects, lambda payload: payload),
    )
    warnings = [probe["warning"] for probe in probes if probe["warning"]]
    return {"status": "verified" if not warnings else "verified_with_warnings", "instance_url": session.instance_url,
            "api_version": session.api_version, "authentication": "direct OAuth access token or client credentials",
            "probes": probes, "warnings": warnings}
