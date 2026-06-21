import importlib
import asyncio
from pathlib import Path

from fastapi.testclient import TestClient
import httpx


def test_demo_scan_creates_manifest_coverage_and_findings(tmp_path, monkeypatch):
    monkeypatch.setenv("SCANNER_DATA_DIR", str(tmp_path))
    import app.main as main
    main = importlib.reload(main)
    with TestClient(main.app) as client:
        response = client.post("/api/scans/demo")
        assert response.status_code == 200, response.text
        scan = response.json()
        assert scan["status"] == "completed"
        assert scan["coverage"]["grade"] == "good"
        assert scan["finding_count"] >= 5

        findings = client.get(f"/api/findings?scan_id={scan['id']}").json()
        titles = {finding["finding_type"] for finding in findings}
        assert "missing_contract_closed_won" in titles
        assert "invalid_stage" in titles


def test_partial_coverage_suppresses_absent_field_rule(tmp_path, monkeypatch):
    monkeypatch.setenv("SCANNER_DATA_DIR", str(tmp_path))
    import app.main as main
    main = importlib.reload(main)
    data = b"Id,Name,StageName\n006AAA,Example,Closed Won\n"
    with TestClient(main.app) as client:
        response = client.post("/api/scans/upload?object_name=Opportunity", files={"file": ("sample.csv", data, "text/csv")})
        assert response.status_code == 200
        scan = response.json()
        assert scan["coverage"]["grade"] == "partial"
        findings = client.get(f"/api/findings?scan_id={scan['id']}").json()
        assert "missing_contract_closed_won" not in {item["finding_type"] for item in findings}


def test_visual_tutorial_is_served(tmp_path, monkeypatch):
    monkeypatch.setenv("SCANNER_DATA_DIR", str(tmp_path))
    import app.main as main
    main = importlib.reload(main)
    with TestClient(main.app) as client:
        response = client.get("/tutorial")
        assert response.status_code == 200
        assert "Machine learning for finding data that looks wrong" in response.text
        assert "Import a CSV into the statistics lab" in response.text


def test_feedback_changes_finding_state(tmp_path, monkeypatch):
    monkeypatch.setenv("SCANNER_DATA_DIR", str(tmp_path))
    import app.main as main
    main = importlib.reload(main)
    with TestClient(main.app) as client:
        scan = client.post("/api/scans/demo").json()
        finding = client.get(f"/api/findings?scan_id={scan['id']}").json()[0]
        response = client.post(f"/api/findings/{finding['id']}/feedback", json={"label": "false_positive"})
        assert response.status_code == 200
        assert response.json()["status"] == "suppressed_until"


def test_direct_salesforce_client_uses_rest_tooling_and_metadata_http_endpoints():
    from app.salesforce import SalesforceClient, SalesforceSession

    paths = []
    def handler(request: httpx.Request) -> httpx.Response:
        paths.append((request.method, request.url.path, request.headers.get("authorization")))
        assert request.headers["authorization"] == "Bearer token"
        if request.url.path.endswith("/limits"):
            return httpx.Response(200, json={"DailyApiRequests": {"Remaining": 100}})
        if request.url.path.endswith("/tooling/query"):
            assert request.url.params["q"] == "SELECT Id FROM ApexClass LIMIT 1"
            return httpx.Response(200, json={"records": [{"Id": "01p"}]})
        if request.url.path.endswith("/services/Soap/m/66.0"):
            assert b"listMetadata" in request.content
            return httpx.Response(200, text="<Envelope xmlns='http://schemas.xmlsoap.org/soap/envelope/' xmlns:m='http://soap.sforce.com/2006/04/metadata'><Body><m:result/><m:result/></Body></Envelope>")
        return httpx.Response(200, json={})

    client = SalesforceClient(SalesforceSession("https://example.my.salesforce.com", "token"), transport=httpx.MockTransport(handler))
    async def calls():
        assert (await client.limits())["DailyApiRequests"]["Remaining"] == 100
        assert len((await client.tooling_query("SELECT Id FROM ApexClass LIMIT 1"))["records"]) == 1
        assert await client.metadata_custom_objects() == 2
    asyncio.run(calls())
    assert paths == [
        ("GET", "/services/data/v66.0/limits", "Bearer token"),
        ("GET", "/services/data/v66.0/tooling/query", "Bearer token"),
        ("POST", "/services/Soap/m/66.0", "Bearer token"),
    ]
