"""Salesforce session and error types (moved verbatim from monolithic salesforce.py)."""
from __future__ import annotations

import os

import httpx
from dataclasses import dataclass


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
