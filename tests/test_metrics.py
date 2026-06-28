from __future__ import annotations

from tests import harness


def test_metrics_after_scan_and_feedback(tmp_path):
    with harness.api_client(tmp_path) as client:
        scan = client.post("/api/scans/demo").json()
        findings = client.get(f"/api/findings?scan_id={scan['id']}").json()
        assert len(findings) == 10

        # Before any feedback
        m0 = client.get("/api/metrics").json()
        assert m0["findings_total"] == 10
        assert m0["open_findings"] == 10
        assert m0["feedback_total"] == 0
        assert m0["acceptance_rate"] is None
        assert m0["false_positive_rate"] is None
        assert sum(m0["by_severity"].values()) == 10

        # Mark one false positive
        fid = findings[0]["id"]
        client.post(f"/api/findings/{fid}/feedback", json={"label": "false_positive"})
        m1 = client.get("/api/metrics").json()
        assert m1["open_findings"] == 9
        assert m1["suppressed"] == 1
        assert m1["feedback_total"] == 1
        assert m1["by_label"].get("false_positive") == 1
        assert m1["false_positive_rate"] == 1.0
        assert m1["acceptance_rate"] == 0.0

        # Mark a real issue
        fid2 = findings[1]["id"]
        client.post(f"/api/findings/{fid2}/feedback", json={"label": "real_issue"})
        m2 = client.get("/api/metrics").json()
        assert m2["feedback_total"] == 2
        assert m2["acceptance_rate"] == 0.5
        assert m2["false_positive_rate"] == 0.5
        assert m2["acknowledged"] == 1
