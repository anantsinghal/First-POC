from __future__ import annotations

import csv
import hashlib
import io
import json
import math
import os
import sqlite3
import statistics
import uuid
from collections import Counter
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastapi import FastAPI, File, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from app.salesforce import SAFE_API_NAME, SalesforceClient, SalesforceError, session_from_environment, verify_direct_api_access


ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = Path(os.environ.get("SCANNER_DATA_DIR", ROOT / "data"))
DB_PATH = DATA_DIR / "scanner.db"
DEMO_FILE = ROOT / "data" / "demo_opportunities.csv"
VALID_OPPORTUNITY_STAGES = {
    "Prospecting", "Qualification", "Needs Analysis", "Value Proposition",
    "Id. Decision Makers", "Perception Analysis", "Proposal/Price Quote",
    "Negotiation/Review", "Closed Won", "Closed Lost", "Proposal",
}

app = FastAPI(title="Salesforce Data Quality Scanner POC", version="0.1.0")
app.mount("/static", StaticFiles(directory=ROOT / "app" / "static"), name="static")


def now() -> str:
    return datetime.now(UTC).isoformat()


@contextmanager
def database():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(DB_PATH)
    connection.row_factory = sqlite3.Row
    try:
        yield connection
        connection.commit()
    finally:
        connection.close()


def setup_database() -> None:
    with database() as conn:
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS scan_manifests (
          id TEXT PRIMARY KEY, source_name TEXT NOT NULL, object_name TEXT NOT NULL,
          status TEXT NOT NULL, created_at TEXT NOT NULL, completed_at TEXT,
          row_count INTEGER NOT NULL DEFAULT 0, source_hash TEXT NOT NULL,
          policy_version TEXT NOT NULL, parser_version TEXT NOT NULL,
          fields_json TEXT NOT NULL, profile_json TEXT, notes TEXT
        );
        CREATE TABLE IF NOT EXISTS coverage (
          scan_id TEXT PRIMARY KEY REFERENCES scan_manifests(id), object_name TEXT NOT NULL,
          observed_records INTEGER NOT NULL, queried_fields_json TEXT NOT NULL,
          missing_fields_json TEXT NOT NULL, suppressed_checks_json TEXT NOT NULL,
          grade TEXT NOT NULL, reason TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS findings (
          id TEXT PRIMARY KEY, fingerprint TEXT UNIQUE NOT NULL, scan_id TEXT NOT NULL,
          object_name TEXT NOT NULL, record_id TEXT NOT NULL, record_name TEXT,
          finding_type TEXT NOT NULL, field_name TEXT, severity TEXT NOT NULL,
          confidence REAL NOT NULL, priority REAL NOT NULL, status TEXT NOT NULL,
          title TEXT NOT NULL, evidence_json TEXT NOT NULL, source_hash TEXT NOT NULL,
          created_at TEXT NOT NULL, last_seen_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS feedback (
          id TEXT PRIMARY KEY, finding_id TEXT NOT NULL REFERENCES findings(id),
          label TEXT NOT NULL, note TEXT, reviewer TEXT NOT NULL, created_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS finding_scan_index ON findings(scan_id);
        CREATE INDEX IF NOT EXISTS finding_status_index ON findings(status);
        """)


@app.on_event("startup")
def on_startup() -> None:
    setup_database()


def serialise_row(row: sqlite3.Row) -> dict[str, Any]:
    item = dict(row)
    for key in ("fields_json", "profile_json", "queried_fields_json", "missing_fields_json",
                "suppressed_checks_json", "evidence_json"):
        if key in item and item[key] is not None:
            item[key.removesuffix("_json")] = json.loads(item.pop(key))
    return item


def clean(value: str | None) -> str:
    return (value or "").strip()


def stable_hash(value: Any) -> str:
    encoded = json.dumps(value, sort_keys=True, default=str, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def display_name(record: dict[str, str]) -> str:
    return clean(record.get("Name")) or clean(record.get("Id"))


def severity_for(rule: str) -> tuple[str, float, float]:
    values = {
        "missing_required": ("high", 0.95, 0.90),
        "invalid_stage": ("high", 0.98, 0.95),
        "missing_account": ("high", 0.95, 0.90),
        "missing_contract_closed_won": ("high", 0.92, 0.86),
        "non_positive_amount": ("medium", 0.92, 0.75),
        "numeric_outlier": ("medium", 0.76, 0.57),
        "rare_combination": ("low", 0.68, 0.36),
    }
    return values[rule]


def create_candidate(
    object_name: str, record: dict[str, str], rule: str, title: str,
    field_name: str | None, evidence: dict[str, Any]
) -> dict[str, Any]:
    severity, confidence, priority = severity_for(rule)
    record_id = clean(record.get("Id"))
    fingerprint = stable_hash({"object": object_name, "record": record_id, "rule": rule, "field": field_name})
    return {
        "id": str(uuid.uuid4()), "fingerprint": fingerprint, "object_name": object_name,
        "record_id": record_id, "record_name": display_name(record), "finding_type": rule,
        "field_name": field_name, "severity": severity, "confidence": confidence,
        "priority": priority, "status": "open", "title": title, "evidence": evidence,
    }


def numeric_values(records: list[dict[str, str]], field: str) -> list[float]:
    numbers: list[float] = []
    for record in records:
        raw = clean(record.get(field))
        try:
            if raw:
                numbers.append(float(raw.replace(",", "")))
        except ValueError:
            continue
    return numbers


def profile_records(records: list[dict[str, str]], fields: list[str]) -> dict[str, Any]:
    profile: dict[str, Any] = {"record_count": len(records), "fields": {}}
    for field in fields:
        values = [clean(row.get(field)) for row in records]
        non_empty = [value for value in values if value]
        details: dict[str, Any] = {
            "null_rate": round(1 - len(non_empty) / len(records), 3) if records else 0,
            "distinct_count": len(set(non_empty)),
            "top_values": Counter(non_empty).most_common(5),
        }
        numbers = numeric_values(records, field)
        if len(numbers) >= 2:
            details["numeric"] = {
                "min": min(numbers), "max": max(numbers),
                "mean": round(statistics.mean(numbers), 2),
                "median": round(statistics.median(numbers), 2),
            }
        profile["fields"][field] = details
    return profile


def calculate_coverage(object_name: str, records: list[dict[str, str]], fields: list[str]) -> dict[str, Any]:
    expected = {"Id"}
    if object_name.lower() == "opportunity":
        expected |= {"Name", "StageName", "Amount", "AccountId", "Contract__c"}
    missing = sorted(expected - set(fields))
    suppressed: list[str] = []
    if "Id" in missing:
        suppressed.append("all record-level checks: Id is absent")
    if object_name.lower() == "opportunity":
        if "StageName" in missing:
            suppressed.append("invalid-stage and closed-won rules: StageName is absent")
        if "Contract__c" in missing:
            suppressed.append("missing-contract rule: Contract__c is absent")
        if "AccountId" in missing:
            suppressed.append("missing-account rule: AccountId is absent")
    if not records or "Id" in missing:
        grade, reason = "blocked", "No trustworthy record-level scan: input is empty or has no Id column."
    elif missing:
        grade, reason = "partial", "Some expected fields are unavailable; dependent checks were suppressed."
    else:
        grade, reason = "good", "Selected POC fields were present in the supplied file."
    return {"object_name": object_name, "observed_records": len(records), "queried_fields": fields,
            "missing_fields": missing, "suppressed_checks": suppressed, "grade": grade, "reason": reason}


def analyze(object_name: str, records: list[dict[str, str]], fields: list[str]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    coverage = calculate_coverage(object_name, records, fields)
    if coverage["grade"] == "blocked":
        return coverage, []
    candidates: list[dict[str, Any]] = []
    has = set(fields)
    for record in records:
        if not clean(record.get("Id")):
            continue
        for required in ("Name",):
            if required in has and not clean(record.get(required)):
                candidates.append(create_candidate(object_name, record, "missing_required", f"Missing required {required}", required,
                    {"rule": "POC required-field check", "observed_value": None, "why": f"{required} is empty."}))
        if object_name.lower() == "opportunity":
            stage = clean(record.get("StageName"))
            if "StageName" in has and stage and stage not in VALID_OPPORTUNITY_STAGES:
                candidates.append(create_candidate(object_name, record, "invalid_stage", "Unknown Opportunity stage", "StageName",
                    {"observed_value": stage, "allowed_values": sorted(VALID_OPPORTUNITY_STAGES), "why": "StageName is not in the POC stage allowlist."}))
            if "AccountId" in has and not clean(record.get("AccountId")):
                candidates.append(create_candidate(object_name, record, "missing_account", "Opportunity has no Account", "AccountId",
                    {"observed_value": None, "why": "The record has no parent AccountId in the extracted data."}))
            if "StageName" in has and "Contract__c" in has and stage == "Closed Won" and not clean(record.get("Contract__c")):
                candidates.append(create_candidate(object_name, record, "missing_contract_closed_won", "Closed Won Opportunity has no contract", "Contract__c",
                    {"stage": stage, "observed_value": None, "why": "POC policy expects Contract__c for Closed Won opportunities."}))
            if "Amount" in has:
                try:
                    if clean(record.get("Amount")) and float(clean(record["Amount"]).replace(",", "")) <= 0:
                        candidates.append(create_candidate(object_name, record, "non_positive_amount", "Opportunity Amount is zero or negative", "Amount",
                            {"observed_value": record.get("Amount"), "why": "POC policy treats non-positive opportunity value as reviewable."}))
                except ValueError:
                    pass

    # Robust numeric candidate generation: IQR only when enough observations exist.
    for field in fields:
        values = sorted(numeric_values(records, field))
        if len(values) < 8:
            continue
        q1, q3 = values[len(values) // 4], values[(len(values) * 3) // 4]
        iqr = q3 - q1
        if iqr <= 0:
            continue
        lower, upper = q1 - 1.5 * iqr, q3 + 1.5 * iqr
        for record in records:
            try:
                value = float(clean(record.get(field)).replace(",", ""))
            except ValueError:
                continue
            if value < lower or value > upper:
                candidates.append(create_candidate(object_name, record, "numeric_outlier", f"Unusual {field} value", field,
                    {"observed_value": value, "method": "IQR", "lower_bound": round(lower, 2), "upper_bound": round(upper, 2),
                     "why": "The value falls outside the 1.5×IQR range of this scan."}))

    # Rare categorical combinations are candidates, not proof of a problem.
    category_fields = [field for field in ("StageName", "Segment__c") if field in has]
    if len(category_fields) == 2 and len(records) >= 8:
        combos = Counter(tuple(clean(row.get(field)) for field in category_fields) for row in records)
        for record in records:
            combo = tuple(clean(record.get(field)) for field in category_fields)
            if all(combo) and combos[combo] == 1:
                candidates.append(create_candidate(object_name, record, "rare_combination", "Rare stage and segment combination", ", ".join(category_fields),
                    {"combination": dict(zip(category_fields, combo)), "occurrences": 1, "population": len(records),
                     "why": "This combination occurs once in the supplied scan. Human review is required."}))
    return coverage, candidates


def persist_scan(source_name: str, object_name: str, raw_bytes: bytes) -> dict[str, Any]:
    # Keep command-line and test use safe even when ASGI lifespan hooks are bypassed.
    setup_database()
    try:
        text = raw_bytes.decode("utf-8-sig")
        reader = csv.DictReader(io.StringIO(text))
        fields = reader.fieldnames or []
        records = [{key: clean(value) for key, value in row.items() if key is not None} for row in reader]
    except (UnicodeDecodeError, csv.Error) as exc:
        raise HTTPException(400, f"Could not parse CSV: {exc}") from exc
    scan_id, source_hash = str(uuid.uuid4()), hashlib.sha256(raw_bytes).hexdigest()
    created = now()
    with database() as conn:
        conn.execute("""INSERT INTO scan_manifests (id, source_name, object_name, status, created_at, row_count, source_hash, policy_version, parser_version, fields_json)
                        VALUES (?, ?, ?, 'running', ?, ?, ?, 'poc-policy-v1', 'poc-parser-v1', ?)""",
                     (scan_id, source_name, object_name, created, len(records), source_hash, json.dumps(fields)))
        coverage, candidates = analyze(object_name, records, fields)
        conn.execute("""INSERT INTO coverage (scan_id, object_name, observed_records, queried_fields_json, missing_fields_json, suppressed_checks_json, grade, reason)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                     (scan_id, object_name, coverage["observed_records"], json.dumps(coverage["queried_fields"]),
                      json.dumps(coverage["missing_fields"]), json.dumps(coverage["suppressed_checks"]), coverage["grade"], coverage["reason"]))
        profile = profile_records(records, fields)
        for candidate in candidates:
            existing = conn.execute("SELECT id, status FROM findings WHERE fingerprint = ?", (candidate["fingerprint"],)).fetchone()
            if existing:
                conn.execute("""UPDATE findings SET scan_id=?, record_name=?, severity=?, confidence=?, priority=?, title=?, evidence_json=?, source_hash=?, last_seen_at=?, status=? WHERE id=?""",
                             (scan_id, candidate["record_name"], candidate["severity"], candidate["confidence"], candidate["priority"], candidate["title"],
                              json.dumps(candidate["evidence"]), source_hash, now(), "reopened" if existing["status"] == "resolved" else existing["status"], existing["id"]))
            else:
                conn.execute("""INSERT INTO findings (id, fingerprint, scan_id, object_name, record_id, record_name, finding_type, field_name, severity, confidence, priority, status, title, evidence_json, source_hash, created_at, last_seen_at)
                                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                             (candidate["id"], candidate["fingerprint"], scan_id, candidate["object_name"], candidate["record_id"], candidate["record_name"], candidate["finding_type"], candidate["field_name"], candidate["severity"], candidate["confidence"], candidate["priority"], candidate["status"], candidate["title"], json.dumps(candidate["evidence"]), source_hash, created, created))
        conn.execute("UPDATE scan_manifests SET status='completed', completed_at=?, profile_json=?, notes=? WHERE id=?",
                     (now(), json.dumps(profile), f"{len(candidates)} candidate occurrences generated.", scan_id))
    return scan_detail(scan_id)


def scan_detail(scan_id: str) -> dict[str, Any]:
    with database() as conn:
        manifest = conn.execute("SELECT * FROM scan_manifests WHERE id=?", (scan_id,)).fetchone()
        if not manifest:
            raise HTTPException(404, "Scan not found")
        coverage = conn.execute("SELECT * FROM coverage WHERE scan_id=?", (scan_id,)).fetchone()
        finding_count = conn.execute("SELECT COUNT(*) FROM findings WHERE scan_id=?", (scan_id,)).fetchone()[0]
    result = serialise_row(manifest)
    result["coverage"] = serialise_row(coverage) if coverage else None
    result["finding_count"] = finding_count
    return result


class FeedbackInput(BaseModel):
    label: str = Field(pattern="^(real_issue|false_positive|valid_exception|needs_review)$")
    note: str = Field(default="", max_length=1000)
    reviewer: str = Field(default="local-reviewer", min_length=1, max_length=120)


class SalesforceScanInput(BaseModel):
    object_name: str = Field(default="Opportunity", min_length=1, max_length=80)
    max_records: int = Field(default=500, ge=1, le=2000)


@app.get("/", include_in_schema=False)
def index() -> FileResponse:
    return FileResponse(ROOT / "app" / "static" / "index.html")


@app.get("/tutorial", include_in_schema=False)
def tutorial() -> FileResponse:
    return FileResponse(ROOT / "app" / "static" / "ml-tutorial.html")


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok", "mode": "local-poc"}


@app.post("/api/scans/demo")
def run_demo_scan() -> dict[str, Any]:
    if not DEMO_FILE.exists():
        raise HTTPException(500, "Demo data is missing")
    return persist_scan(DEMO_FILE.name, "Opportunity", DEMO_FILE.read_bytes())


@app.post("/api/salesforce/verify")
async def verify_salesforce() -> dict[str, Any]:
    """Prove direct access to REST/Describe, Tooling, and Metadata SOAP APIs."""
    try:
        return await verify_direct_api_access()
    except SalesforceError as exc:
        raise HTTPException(409 if exc.status_code is None else 502, str(exc)) from exc


@app.post("/api/scans/salesforce")
async def scan_salesforce(input: SalesforceScanInput) -> dict[str, Any]:
    """Extract a bounded object slice via direct REST API, then run the same scanner."""
    if not SAFE_API_NAME.fullmatch(input.object_name):
        raise HTTPException(400, "Invalid Salesforce object API name")
    try:
        session = await session_from_environment()
        client = SalesforceClient(session)
        describe = await client.describe_sobject(input.object_name)
        readable = {field.get("name") for field in describe.get("fields", []) if field.get("name") and field.get("queryable")}
        policy_fields = ["Id", "Name", "StageName", "Amount", "AccountId", "Contract__c", "Segment__c", "CloseDate"]
        fields = [field for field in policy_fields if field in readable]
        if "Id" not in fields:
            raise HTTPException(422, "The integration user cannot query Id for this object; no trustworthy scan is possible.")
        records = await client.query_records(f"SELECT {', '.join(fields)} FROM {input.object_name}", max_records=input.max_records)
    except SalesforceError as exc:
        raise HTTPException(502 if exc.status_code else 409, str(exc)) from exc

    output = io.StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=fields, extrasaction="ignore")
    writer.writeheader()
    writer.writerows([{field: record.get(field, "") for field in fields} for record in records])
    safe_host = session.instance_url.split("//", 1)[-1]
    return persist_scan(f"salesforce:{safe_host}/{input.object_name}", input.object_name, output.getvalue().encode("utf-8"))


@app.post("/api/scans/upload")
async def upload_scan(object_name: str = Query(min_length=1, max_length=80), file: UploadFile = File(...)) -> dict[str, Any]:
    if not (file.filename or "").lower().endswith(".csv"):
        raise HTTPException(400, "Upload a CSV file")
    raw = await file.read()
    if len(raw) > 25_000_000:
        raise HTTPException(413, "POC upload limit is 25 MB")
    return persist_scan(file.filename or "upload.csv", object_name, raw)


@app.get("/api/scans")
def list_scans() -> list[dict[str, Any]]:
    with database() as conn:
        rows = conn.execute("SELECT * FROM scan_manifests ORDER BY created_at DESC LIMIT 50").fetchall()
    return [serialise_row(row) for row in rows]


@app.get("/api/scans/{scan_id}")
def get_scan(scan_id: str) -> dict[str, Any]:
    return scan_detail(scan_id)


@app.get("/api/findings")
def list_findings(scan_id: str | None = None, status: str | None = None) -> list[dict[str, Any]]:
    clauses, params = [], []
    if scan_id:
        clauses.append("scan_id=?"); params.append(scan_id)
    if status:
        clauses.append("status=?"); params.append(status)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    with database() as conn:
        rows = conn.execute(f"SELECT * FROM findings {where} ORDER BY priority DESC, last_seen_at DESC", params).fetchall()
        result = []
        for row in rows:
            finding = serialise_row(row)
            feedback = conn.execute("SELECT * FROM feedback WHERE finding_id=? ORDER BY created_at DESC", (finding["id"],)).fetchall()
            finding["feedback"] = [dict(item) for item in feedback]
            result.append(finding)
    return result


@app.post("/api/findings/{finding_id}/feedback")
def add_feedback(finding_id: str, feedback: FeedbackInput) -> dict[str, Any]:
    state = {"real_issue": "acknowledged", "false_positive": "suppressed_until", "valid_exception": "suppressed_until", "needs_review": "open"}[feedback.label]
    with database() as conn:
        if not conn.execute("SELECT 1 FROM findings WHERE id=?", (finding_id,)).fetchone():
            raise HTTPException(404, "Finding not found")
        conn.execute("INSERT INTO feedback (id, finding_id, label, note, reviewer, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                     (str(uuid.uuid4()), finding_id, feedback.label, feedback.note, feedback.reviewer, now()))
        conn.execute("UPDATE findings SET status=? WHERE id=?", (state, finding_id))
    return {"finding_id": finding_id, "status": state, "label": feedback.label}


@app.get("/api/dashboard")
def dashboard() -> dict[str, Any]:
    with database() as conn:
        latest = conn.execute("SELECT id FROM scan_manifests ORDER BY created_at DESC LIMIT 1").fetchone()
        total_scans = conn.execute("SELECT COUNT(*) FROM scan_manifests").fetchone()[0]
        open_findings = conn.execute("SELECT COUNT(*) FROM findings WHERE status IN ('open','reopened')").fetchone()[0]
    return {"total_scans": total_scans, "open_findings": open_findings, "latest_scan": scan_detail(latest[0]) if latest else None}
