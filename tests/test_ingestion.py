from __future__ import annotations

import asyncio
import hashlib
from pathlib import Path

import httpx
import pytest

from app.ingestion.base import IngestionError
from app.ingestion.csv_source import CsvSource
from app.ingestion.salesforce_source import SalesforceObjectSource
from app.salesforce import (
    SAFE_API_NAME,
    SalesforceClient,
    SalesforceError,
    SalesforceSession,
    session_from_environment,
    verify_direct_api_access,
)


def test_salesforce_package_relocation_smoke():
    """All 6 public names must be importable from app.salesforce (package re-exports)."""
    from app.salesforce import (
        SAFE_API_NAME,
        SalesforceClient,
        SalesforceError,
        SalesforceSession,
        session_from_environment,
        verify_direct_api_access,
    )
    assert SAFE_API_NAME is not None
    assert SalesforceClient is not None
    assert SalesforceError is not None
    assert SalesforceSession is not None
    assert session_from_environment is not None
    assert verify_direct_api_access is not None


def test_csv_source_loads_demo_data():
    """CsvSource on real demo file produces correct LoadedDataset (matches baseline parse)."""
    demo_path = Path(__file__).resolve().parents[1] / "data" / "demo_opportunities.csv"
    raw = demo_path.read_bytes()
    src = CsvSource(raw_bytes=raw, object_name="Opportunity", source_name="demo_opportunities.csv")
    ds = asyncio.run(src.load())
    assert ds.object_name == "Opportunity"
    assert ds.source_name == "demo_opportunities.csv"
    assert ds.fields == ["Id", "Name", "StageName", "Amount", "AccountId", "Contract__c", "Segment__c", "CloseDate"]
    assert len(ds.records) == 11
    assert ds.source_hash == hashlib.sha256(raw).hexdigest()
    # sample cleaned record
    assert isinstance(ds.records[0]["Id"], str)
    assert ds.records[0]["Id"]


def test_csv_source_bad_bytes_raises_ingestion_error():
    """Bad UTF or csv content raises IngestionError (with 400)."""
    bad = b"\xff\xfe\x00bad"
    src = CsvSource(raw_bytes=bad, object_name="X", source_name="bad.csv")
    with pytest.raises(IngestionError) as exc:
        asyncio.run(src.load())
    assert exc.value.status_code == 400

    # another non-decodable bytes case (DictReader itself is lenient on most 'malformed' csv)
    bad2 = b"\x80\x81\x82 invalid utf-8 start bytes"
    src2 = CsvSource(raw_bytes=bad2, object_name="X", source_name="bad2.csv")
    with pytest.raises(IngestionError):
        asyncio.run(src2.load())


def test_salesforce_object_source_success_with_mock():
    """SalesforceObjectSource with MockTransport for describe + query succeeds with Id present."""
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/sobjects/Opportunity/describe"):
            return httpx.Response(
                200,
                json={
                    "fields": [
                        {"name": "Id", "queryable": True},
                        {"name": "Name", "queryable": True},
                        {"name": "StageName", "queryable": True},
                    ]
                },
            )
        if "/query" in str(request.url):
            return httpx.Response(
                200,
                json={
                    "records": [
                        {"Id": "006001", "Name": "Acme", "StageName": "Prospecting", "attributes": {}},
                        {"Id": "006002", "Name": "Globex", "StageName": "Closed Won", "attributes": {}},
                    ]
                },
            )
        return httpx.Response(200, json={})

    session = SalesforceSession("https://example.my.salesforce.com", "token")
    client = SalesforceClient(session, transport=httpx.MockTransport(handler))
    src = SalesforceObjectSource(
        client=client, session=session, object_name="Opportunity", max_records=10
    )
    ds = asyncio.run(src.load())
    assert ds.fields == ["Id", "Name", "StageName"]
    assert len(ds.records) == 2
    assert ds.records[0]["Id"] == "006001"
    assert ds.records[0]["Name"] == "Acme"
    assert all(isinstance(v, str) for rec in ds.records for v in rec.values())
    assert "Id" in ds.fields


def test_salesforce_object_source_raises_on_missing_id():
    """When describe shows Id not queryable, load raises IngestionError status_code=422."""
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/sobjects/Opportunity/describe"):
            return httpx.Response(
                200,
                json={
                    "fields": [
                        {"name": "Name", "queryable": True},
                        {"name": "StageName", "queryable": True},
                        # deliberately no Id
                    ]
                },
            )
        return httpx.Response(200, json={})

    session = SalesforceSession("https://example.my.salesforce.com", "token")
    client = SalesforceClient(session, transport=httpx.MockTransport(handler))
    src = SalesforceObjectSource(client=client, session=session, object_name="Opportunity")
    with pytest.raises(IngestionError) as exc:
        asyncio.run(src.load())
    assert exc.value.status_code == 422
    assert "cannot query Id" in str(exc.value)
