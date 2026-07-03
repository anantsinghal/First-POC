"""Salesforce REST client and SAFE_API_NAME (moved verbatim from monolithic salesforce.py)."""
from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from collections.abc import AsyncIterator
from time import perf_counter
from typing import Any

import httpx

from .session import SalesforceError, SalesforceSession


SAFE_API_NAME = re.compile(r"^[A-Za-z][A-Za-z0-9_]*$")


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

    async def bulk_create_query_job(self, soql: str) -> str:
        """Create Bulk API 2.0 query job; return the job id."""
        payload = {"operation": "query", "query": soql}
        response = await self._request(
            "POST",
            f"{self.data_base_url}/jobs/query",
            json=payload,
            headers={"Content-Type": "application/json"},
        )
        data = response.json()
        return data["id"]

    async def bulk_job_status(self, job_id: str) -> str:
        """Return job state; raise SalesforceError (with errorMessage) on Failed/Aborted."""
        response = await self._request("GET", f"{self.data_base_url}/jobs/query/{job_id}")
        data = response.json()
        state = data.get("state", "")
        if state in ("Failed", "Aborted"):
            msg = data.get("errorMessage") or f"Job {state}"
            raise SalesforceError(response.status_code or 502, msg)
        return state

    async def bulk_job_results_pages(self, job_id: str, *, max_records: int) -> AsyncIterator[str]:
        """Async generator of raw CSV result pages; stop on 'null' locator or record budget."""
        base = f"{self.data_base_url}/jobs/query/{job_id}/results"
        headers = {"Accept": "text/csv"}
        locator: str | None = None
        records_yielded = 0
        while True:
            url = base if not locator else f"{base}?locator={locator}"
            response = await self._request("GET", url, headers=headers)
            text = response.text
            yield text
            # approx record count (header + data lines)
            if text:
                lines = [ln for ln in text.splitlines() if ln.strip()]
                page_recs = max(0, len(lines) - 1)
                records_yielded += page_recs
            locator = response.headers.get("Sforce-Locator")
            if locator is None:
                break
            if str(locator).lower() == "null":
                break
            if records_yielded >= max_records:
                break

    @staticmethod
    def _validate_object(object_name: str) -> None:
        if not SAFE_API_NAME.fullmatch(object_name):
            raise SalesforceError(400, "Invalid Salesforce object API name.")
