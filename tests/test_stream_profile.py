from __future__ import annotations

import io

import pytest

from app.coverage.grader import CoverageGrader
from app.domain.enums import CoverageGrade
from app.ingestion.csv_source import CsvSource
from app.policy.field_policy import DEFAULT_POLICY
from tests import harness


def test_coverage_grade_from_record_count():
    """Streaming callers can grade from a count without a record list."""
    grader = CoverageGrader(DEFAULT_POLICY)
    cov_empty = grader.grade("Account", [], ["Id", "Name"], record_count=0)
    cov_good = grader.grade("Account", [], ["Id", "Name"], record_count=5000)
    assert cov_empty.grade == CoverageGrade.BLOCKED
    assert cov_good.grade == CoverageGrade.GOOD
    assert cov_good.observed_records == 5000


def test_csv_source_iter_batches_streams_without_full_list():
    """iter_batches yields cleaned batches lazily and records the field names."""
    buf = io.StringIO()
    buf.write("Id,Name\n")
    for i in range(25):
        buf.write(f"00Q{i:03d}, R{i} \n")  # trailing spaces -> clean()
    src = CsvSource(raw_bytes=buf.getvalue().encode(), object_name="Account", source_name="x.csv")
    batches = list(src.iter_batches(batch_size=10))
    assert [len(b) for b in batches] == [10, 10, 5]
    assert src.fieldnames == ["Id", "Name"]
    assert batches[0][0] == {"Id": "00Q000", "Name": "R0"}  # cleaned


def test_profile_scan_endpoint_streaming(tmp_path):
    pytest.importorskip("duckdb")
    pytest.importorskip("pyarrow")
    # Build a CSV with a numeric column and two segments.
    rows = ["Id,Segment,Amount"]
    for i in range(1500):
        rows.append(f"00Q{i:05d},{'Enterprise' if i % 2 == 0 else 'SMB'},{1000 + i}")
    csv_bytes = ("\n".join(rows) + "\n").encode()

    with harness.api_client(tmp_path) as client:
        resp = client.post(
            "/api/scans/profile?object_name=Account",
            files=[("file", ("accounts.csv", csv_bytes, "text/csv"))],
        )
        assert resp.status_code == 200, resp.text
        scan = resp.json()
        assert scan["status"] == "completed"
        assert scan["finding_count"] == 0  # profile-only: no detection
        prof = scan["profile"]
        assert prof["record_count"] == 1500
        assert prof["fields"]["Segment"]["distinct_count"] == 2
        assert prof["fields"]["Amount"]["numeric"]["min"] == 1000.0
        assert prof["fields"]["Amount"]["numeric"]["max"] == 2499.0
        # An audit 'scan_profiled' entry was written.
        audit = client.get("/api/audit").json()
        assert any(a["event"] == "scan_profiled" for a in audit)
