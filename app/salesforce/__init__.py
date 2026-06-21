"""Salesforce package re-exports (keeps `from app.salesforce import ...` working verbatim)."""
from __future__ import annotations

from .client import SAFE_API_NAME, SalesforceClient
from .session import SalesforceError, SalesforceSession, session_from_environment
from .verify import verify_direct_api_access

__all__ = [
    "SAFE_API_NAME",
    "SalesforceClient",
    "SalesforceError",
    "SalesforceSession",
    "session_from_environment",
    "verify_direct_api_access",
]
