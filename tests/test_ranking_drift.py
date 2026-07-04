from __future__ import annotations

from pathlib import Path

from app.api.container import build_container
from app.services.ranking_service import RankingService
from tests import harness


def _upload_csv(client, *, object_name: str, headers: list[str], rows: list[list[str]]) -> dict:
    response = client.post(
        f"/api/scans/upload?object_name={object_name}",
        files={"file": ("upload.csv", harness.csv_bytes(headers, rows), "text/csv")},
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_ranking_sinks_labeled_down_type(tmp_path: Path):
    with harness.api_client(tmp_path) as client:
        scan = client.post("/api/scans/demo").json()
        findings = client.get(f"/api/findings?scan_id={scan['id']}").json()
        rare = [finding for finding in findings if finding["finding_type"] == "rare_combination"]
        assert len(rare) >= 3
        for finding in rare[:3]:
            response = client.post(
                f"/api/findings/{finding['id']}/feedback",
                json={"label": "false_positive"},
            )
            assert response.status_code == 200

    container = build_container(data_dir=tmp_path, root=harness.ROOT)
    ranker = RankingService(database=container.database, feedback=container.finding_service.feedback, min_labels=3)
    ranked = ranker.rank(container.finding_service.list_findings(scan_id=scan["id"]))

    unlabeled = next(finding for finding in ranked if finding["finding_type"] == "invalid_stage")
    assert unlabeled["rank_score"] == unlabeled["priority"]
    assert unlabeled["type_acceptance"] is None

    scores = [finding["rank_score"] for finding in ranked]
    assert scores == sorted(scores, reverse=True)
    for finding in ranked:
        if finding["finding_type"] == "rare_combination":
            assert finding["rank_score"] < finding["priority"]
            assert finding["type_acceptance"] < 0.5


def test_ranking_below_min_labels_leaves_priority_unchanged(tmp_path: Path):
    with harness.api_client(tmp_path) as client:
        scan = client.post("/api/scans/demo").json()
        findings = client.get(f"/api/findings?scan_id={scan['id']}").json()
        rare = [finding for finding in findings if finding["finding_type"] == "rare_combination"]
        for finding in rare[:3]:
            response = client.post(
                f"/api/findings/{finding['id']}/feedback",
                json={"label": "false_positive"},
            )
            assert response.status_code == 200

    container = build_container(data_dir=tmp_path, root=harness.ROOT)
    ranker = RankingService(database=container.database, feedback=container.finding_service.feedback, min_labels=5)
    ranked = ranker.rank(container.finding_service.list_findings(scan_id=scan["id"]))

    for finding in ranked:
        assert finding["rank_score"] == finding["priority"]


def test_metrics_api_includes_type_acceptance(tmp_path: Path):
    with harness.api_client(tmp_path) as client:
        response = client.get("/api/metrics")
        assert response.status_code == 200
        payload = response.json()
        assert "type_acceptance" in payload
        assert isinstance(payload["type_acceptance"], dict)


def test_drift_endpoint_detects_volume_null_and_mean_shift(tmp_path: Path):
    headers = ["Id", "Name", "Color", "Amount"]
    rows_v1 = [
        [f"WID-{idx:03d}", f"Widget {idx}", "Blue", str(95 + idx)]
        for idx in range(20)
    ]
    rows_v2 = [
        [
            f"WID2-{idx:03d}",
            f"Widget v2 {idx}",
            "" if idx % 2 == 0 else "Blue",
            str(980 + idx * 2),
        ]
        for idx in range(40)
    ]

    with harness.api_client(tmp_path) as client:
        scan1 = _upload_csv(client, object_name="Widget__c", headers=headers, rows=rows_v1)
        drift1 = client.get(f"/api/scans/{scan1['id']}/drift")
        assert drift1.status_code == 200
        payload1 = drift1.json()
        assert payload1["baseline_scan_id"] is None
        assert payload1["drift_count"] == 0

        scan2 = _upload_csv(client, object_name="Widget__c", headers=headers, rows=rows_v2)
        drift2 = client.get(f"/api/scans/{scan2['id']}/drift")
        assert drift2.status_code == 200
        payload2 = drift2.json()
        kinds = {drift["kind"] for drift in payload2["drifts"]}
        assert payload2["baseline_scan_id"] == scan1["id"]
        assert payload2["drift_count"] >= 3
        assert {"volume_swing", "null_rate_shift", "mean_shift"}.issubset(kinds)

        audit = client.get("/api/audit").json()
        drift_events = [entry for entry in audit if entry["event"] == "drift_detected"]
        assert drift_events
        assert drift_events[0]["detail"]["drift_count"] == payload2["drift_count"]
        assert set(drift_events[0]["detail"]["kinds"]) == kinds


def test_drift_unknown_scan_returns_404(tmp_path: Path):
    with harness.api_client(tmp_path) as client:
        response = client.get("/api/scans/does-not-exist/drift")
        assert response.status_code == 404
