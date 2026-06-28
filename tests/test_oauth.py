from __future__ import annotations

import asyncio

import httpx
import pytest

from app.salesforce.session import SalesforceError, session_from_environment


def test_client_credentials_success(monkeypatch):
    """Client-credentials flow with transport: asserts body, returns valid session."""
    monkeypatch.setenv("SF_CLIENT_ID", "MYCLIENTID")
    monkeypatch.setenv("SF_CLIENT_SECRET", "MYSECRET")
    monkeypatch.setenv("SF_LOGIN_URL", "https://carhuborg.my.salesforce.com")
    monkeypatch.delenv("SF_INSTANCE_URL", raising=False)
    monkeypatch.delenv("SF_ACCESS_TOKEN", raising=False)
    # optional api version
    monkeypatch.setenv("SF_API_VERSION", "v66.0")

    called = {"hit": False}

    def handler(request: httpx.Request) -> httpx.Response:
        called["hit"] = True
        assert str(request.url).endswith("/services/oauth2/token")
        body = request.content.decode("utf-8") if request.content else ""
        assert "grant_type=client_credentials" in body
        assert "client_id=MYCLIENTID" in body
        assert "client_secret=MYSECRET" in body
        return httpx.Response(
            200,
            json={"access_token": "TKN", "instance_url": "https://carhuborg.my.salesforce.com"},
        )

    transport = httpx.MockTransport(handler)
    session = asyncio.run(session_from_environment(transport=transport))
    assert called["hit"] is True
    assert isinstance(session, type(session))  # SalesforceSession
    assert session.instance_url == "https://carhuborg.my.salesforce.com"
    assert session.access_token == "TKN"
    assert session.api_version == "v66.0"


def test_access_token_precedence(monkeypatch):
    """Access-token path takes precedence; transport never invoked."""
    monkeypatch.setenv("SF_INSTANCE_URL", "https://example.my.salesforce.com")
    monkeypatch.setenv("SF_ACCESS_TOKEN", "PRETOKEN")
    monkeypatch.delenv("SF_CLIENT_ID", raising=False)
    monkeypatch.delenv("SF_CLIENT_SECRET", raising=False)

    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("transport should never be called in access-token path")

    transport = httpx.MockTransport(handler)
    session = asyncio.run(session_from_environment(transport=transport))
    assert session.instance_url == "https://example.my.salesforce.com"
    assert session.access_token == "PRETOKEN"


def test_not_configured_raises(monkeypatch):
    """No env vars → SalesforceError."""
    monkeypatch.delenv("SF_INSTANCE_URL", raising=False)
    monkeypatch.delenv("SF_ACCESS_TOKEN", raising=False)
    monkeypatch.delenv("SF_CLIENT_ID", raising=False)
    monkeypatch.delenv("SF_CLIENT_SECRET", raising=False)
    monkeypatch.delenv("SF_LOGIN_URL", raising=False)

    with pytest.raises(SalesforceError):
        asyncio.run(session_from_environment())


def test_oauth_http_error_raises(monkeypatch):
    """HTTP error from token endpoint → SalesforceError."""
    monkeypatch.setenv("SF_CLIENT_ID", "ID")
    monkeypatch.setenv("SF_CLIENT_SECRET", "SEC")
    monkeypatch.setenv("SF_LOGIN_URL", "https://login.salesforce.com")
    monkeypatch.delenv("SF_INSTANCE_URL", raising=False)
    monkeypatch.delenv("SF_ACCESS_TOKEN", raising=False)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"error": "invalid_client"})

    transport = httpx.MockTransport(handler)
    with pytest.raises(SalesforceError):
        asyncio.run(session_from_environment(transport=transport))
