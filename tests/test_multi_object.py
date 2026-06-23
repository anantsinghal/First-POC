from __future__ import annotations

import asyncio
import importlib
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.detection.base import DetectionContext
from app.detection.registry import build_default_registry
from app.ingestion.base import IngestionError
from app.ingestion.multi_csv_source import MultiCsvSource
from app.policy.field_policy import DEFAULT_POLICY


def _account_csv() -> bytes:
    rows = ["Id,Name"]
    rows.extend(f"acc{i:03d},Account {i:03d}" for i in range(20))
    return ("\n".join(rows) + "\n").encode()


def _contact_csv() -> bytes:
    rows = ["Id,AccountId,Name"]
    rows.extend(f"con{i:03d},acc{i:03d},Contact {i:03d}" for i in range(17))
    return ("\n".join(rows) + "\n").encode()


def test_multi_csv_source_loads_related_records_and_detects_missing_children() -> None:
    source = MultiCsvSource(
        primary_object="Account",
        files=[("Account", _account_csv()), ("Contact", _contact_csv())],
        source_name="multi:Account+1 related",
    )

    dataset = asyncio.run(source.load())

    assert dataset.object_name == "Account"
    assert len(dataset.records) == 20
    assert dataset.related_records is not None
    assert len(dataset.related_records["Contact"]) == 17
    assert dataset.source_hash

    ctx = DetectionContext(
        object_name=dataset.object_name,
        records=dataset.records,
        fields=dataset.fields,
        policy=DEFAULT_POLICY,
        related_records=dataset.related_records or {},
    )
    findings = build_default_registry().detect(ctx)
    missing_children = [f for f in findings if f.finding_type == "missing_children"]
    assert len(missing_children) == 3


def test_multi_csv_source_primary_missing_raises_400() -> None:
    source = MultiCsvSource(
        primary_object="Nope",
        files=[("Account", _account_csv())],
        source_name="multi:Nope+0 related",
    )

    with pytest.raises(IngestionError) as exc:
        asyncio.run(source.load())

    assert exc.value.status_code == 400


def _client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv("SCANNER_DATA_DIR", str(tmp_path))
    import app.main

    importlib.reload(app.main)
    return TestClient(app.main.app)


def test_api_multi_scan_two_files_produces_missing_children(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with _client(tmp_path, monkeypatch) as client:
        response = client.post(
            "/api/scans/multi?primary_object=Account",
            files=[
                ("files", ("Account.csv", _account_csv(), "text/csv")),
                ("files", ("Contact.csv", _contact_csv(), "text/csv")),
            ],
        )

        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "completed"

        findings = client.get(f"/api/findings?scan_id={body['id']}").json()
        assert "missing_children" in {f["finding_type"] for f in findings}


def test_api_multi_scan_non_csv_file_returns_400(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with _client(tmp_path, monkeypatch) as client:
        response = client.post(
            "/api/scans/multi?primary_object=Account",
            files=[("files", ("Account.txt", _account_csv(), "text/plain"))],
        )

    assert response.status_code == 400


def test_api_multi_scan_primary_not_found_returns_400(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with _client(tmp_path, monkeypatch) as client:
        response = client.post(
            "/api/scans/multi?primary_object=Nope",
            files=[("files", ("Account.csv", _account_csv(), "text/csv"))],
        )

    assert response.status_code == 400
