"""Direct Salesforce HTTP client used by the POC; it never shells out to sf/sfdx."""
from __future__ import annotations

import asyncio
import os
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from time import perf_counter
from typing import Any

import httpx


SAFE_API_NAME = re.compile(r"^[A-Za-z][A-Za-z0-9_]*$")


class SalesforceError(RuntimeError):
    def __init__(self, status_code: int | None, message: str) -> None:
        self.status_code = status_code
        super().__init__(message)


@dataclass(frozen=True)
class SalesforceSession:
    instance_url: str
    access_token: str
    api_version: str = "v66.0"


async def session_from_environment() -> SalesforceSession:
    instance_url = os.getenv("SF_INSTANCE_URL", "").strip()
    access_token = os.getenv("SF_ACCESS_TOKEN", "").strip()
    api_version = os.getenv("SF_API_VERSION", "v66.0").strip() or "v66.0"
    if instance_url and access_token:
        return SalesforceSession(instance_url.rstrip("/"), access_token, api_version)

    login_url = os.getenv("SF_LOGIN_URL", "https://login.salesforce.com").strip().rstrip("/")
    client_id = os.getenv("SF_CLIENT_ID", "").strip()
    client_secret = os.getenv("SF_CLIENT_SECRET", "").strip()
    if not client_id or not client_secret:
        raise SalesforceError(None, "Salesforce is not configured. Set SF_INSTANCE_URL + SF_ACCESS_TOKEN, or Connected App credentials.")
    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.post(
            f"{login_url}/services/oauth2/token",
            data={"grant_type": "client_credentials", "client_id": client_id, "client_secret": client_secret},
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
    if response.is_error:
        raise SalesforceError(response.status_code, "Salesforce OAuth client-credentials request failed.")
    payload = response.json()
    if not payload.get("access_token") or not payload.get("instance_url"):
        raise SalesforceError(502, "Salesforce OAuth response did not contain an access token and instance URL.")
    return SalesforceSession(payload["instance_url"].rstrip("/"), payload["access_token"], api_version)


class SalesforceClient:
    def __init__(self, session: SalesforceSession, *, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self.session = session
        self.transport = transport

    @property
    def data_base_url(self) -> str:
        return f"{self.session.instance_url}/services/data/{self.session.api_version}"

    @property
    def metadata_url(self) -> str:
        return f"{self.session.instance_url}/services/Soap/m/{self.session.api_version.removeprefix('v')}"

    async def _request(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
        headers = {"Authorization": f"Bearer {self.session.access_token}", **kwargs.pop("headers", {})}
        async with httpx.AsyncClient(timeout=60, transport=self.transport) as client:
            response = await client.request(method, url, headers=headers, **kwargs)
        if response.is_error:
            raise SalesforceError(response.status_code, f"Salesforce {method} request failed with HTTP {response.status_code}.")
        return response

    async def json(self, method: str, path: str, *, params: dict[str, Any] | None = None) -> dict[str, Any]:
        url = path if path.startswith("http") else f"{self.data_base_url}/{path.lstrip('/')}"
        response = await self._request(method, url, params=params)
        if response.status_code == 204:
            return {}
        return response.json()

    async def limits(self) -> dict[str, Any]:
        return await self.json("GET", "/limits")

    async def describe_global(self) -> dict[str, Any]:
        return await self.json("GET", "/sobjects")

    async def describe_sobject(self, object_name: str) -> dict[str, Any]:
        self._validate_object(object_name)
        return await self.json("GET", f"/sobjects/{object_name}/describe")

    async def tooling_query(self, query: str) -> dict[str, Any]:
        return await self.json("GET", "/tooling/query", params={"q": query})

    async def query_records(self, soql: str, *, max_records: int = 500) -> list[dict[str, Any]]:
        payload = await self.json("GET", "/query", params={"q": soql})
        records = list(payload.get("records") or [])
        next_url = payload.get("nextRecordsUrl")
        while next_url and len(records) < max_records:
            page = await self._request("GET", f"{self.session.instance_url}{next_url}")
            payload = page.json()
            records.extend(payload.get("records") or [])
            next_url = payload.get("nextRecordsUrl")
        return records[:max_records]

    async def metadata_custom_objects(self) -> int:
        envelope = f'''<?xml version="1.0" encoding="UTF-8"?>
<env:Envelope xmlns:env="http://schemas.xmlsoap.org/soap/envelope/" xmlns:met="http://soap.sforce.com/2006/04/metadata">
  <env:Header><met:SessionHeader><met:sessionId>{self.session.access_token}</met:sessionId></met:SessionHeader></env:Header>
  <env:Body><met:listMetadata><met:queries><met:type>CustomObject</met:type></met:queries><met:asOfVersion>{self.session.api_version.removeprefix('v')}</met:asOfVersion></met:listMetadata></env:Body>
</env:Envelope>'''
        response = await self._request("POST", self.metadata_url, content=envelope.encode(), headers={"Content-Type": "text/xml; charset=UTF-8", "SOAPAction": ""})
        root = ET.fromstring(response.text)
        return len(root.findall(".//{http://soap.sforce.com/2006/04/metadata}result"))

    @staticmethod
    def _validate_object(object_name: str) -> None:
        if not SAFE_API_NAME.fullmatch(object_name):
            raise SalesforceError(400, "Invalid Salesforce object API name.")


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
