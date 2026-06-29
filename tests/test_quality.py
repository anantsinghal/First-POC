from __future__ import annotations

from pathlib import Path

from tests import harness


def test_demo_scan_quality(tmp_path: Path) -> None:
    """Demo scan exposes per-scan quality metrics with observed-record coverage."""
    with harness.api_client(tmp_path) as client:
        scan_response = client.post("/api/scans/demo")
        assert scan_response.status_code == 200
        scan_id = scan_response.json()["id"]

        response = client.get(f"/api/scans/{scan_id}/quality")
        assert response.status_code == 200

        payload = response.json()
        assert payload["observed_records"] == 11
        assert 0 <= payload["quality_score"] <= 100
        assert 1 <= payload["flagged_records"] <= 11
        assert payload["clean_record_rate"] == round(
            1 - payload["flagged_records"] / payload["observed_records"], 4
        )
        assert payload["previous_scan_id"] is None


def test_demo_scan_quality_trend(tmp_path: Path) -> None:
    """A second identical demo scan reports the prior scan and zero delta."""
    with harness.api_client(tmp_path) as client:
        first = client.post("/api/scans/demo")
        assert first.status_code == 200
        first_scan_id = first.json()["id"]

        second = client.post("/api/scans/demo")
        assert second.status_code == 200
        second_scan_id = second.json()["id"]

        response = client.get(f"/api/scans/{second_scan_id}/quality")
        assert response.status_code == 200

        payload = response.json()
        assert payload["previous_scan_id"] == first_scan_id
        assert payload["quality_delta"] == 0.0


def test_scan_quality_not_found(tmp_path: Path) -> None:
    """Unknown scans return a 404."""
    with harness.api_client(tmp_path) as client:
        response = client.get("/api/scans/does-not-exist/quality")
        assert response.status_code == 404
