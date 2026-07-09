"""Accuracy benchmark with planted ground truth.

Black-box exercise of the real engine via ``app.api.container.build_container``.
No engine changes — tools/ + tests/ + docs only.

Run: ``python -m tools.benchmark --rows 2000 --seed 1234 --out docs``
"""

from __future__ import annotations

import argparse
import asyncio
import json
import random
import sqlite3
import sys
from datetime import date
from pathlib import Path
from typing import Any

from app.api.container import build_container
from app.ingestion.sqlite_source import SqliteTableSource

REPO_ROOT = Path(__file__).resolve().parents[1]

FAMILY_MAP: dict[str, set[str]] = {
    "picklist_invalid": {"picklist_invalid"},
    "required_missing": {"required_missing"},
    "orphan_reference": {"orphan_reference"},
    "invalid_format": {"invalid_format"},
    "stat_outlier": {"numeric_outlier", "robust_outlier"},
}

CONTRACT_FAMILIES: tuple[str, ...] = (
    "picklist_invalid",
    "required_missing",
    "orphan_reference",
    "invalid_format",
)

STAT_FINDING_TYPES: frozenset[str] = frozenset(
    {"numeric_outlier", "robust_outlier", "ml_anomaly"}
)

FAMILY_ORDER: tuple[str, ...] = (
    "picklist_invalid",
    "required_missing",
    "orphan_reference",
    "invalid_format",
    "stat_outlier",
)


def generate_dataset(dir: Path, *, rows: int = 2000, seed: int = 1234) -> dict:
    """Build seeded SQLite bench.db + truth.json with planted anomalies.

    Returns ``{"db_path", "table": "deals", "truth": [...], "rows_total"}``.
    """
    out = Path(dir)
    out.mkdir(parents=True, exist_ok=True)
    db_path = out / "bench.db"
    if db_path.exists():
        db_path.unlink()

    rnd = random.Random(seed)
    stages = ("open", "won", "lost")
    truth: list[dict[str, Any]] = []

    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            "CREATE TABLE owners ("
            "id INTEGER PRIMARY KEY, "
            "name TEXT NOT NULL"
            ")"
        )
        conn.executemany(
            "INSERT INTO owners (id, name) VALUES (?, ?)",
            [(i, f"Owner {i}") for i in range(1, 21)],
        )
        conn.execute(
            "CREATE TABLE deals ("
            "id INTEGER PRIMARY KEY, "
            "owner_id INTEGER NOT NULL REFERENCES owners(id), "
            "stage TEXT NOT NULL CHECK (stage IN ('open','won','lost')), "
            "amount REAL, "
            "email TEXT, "
            "website TEXT"
            ")"
        )

        clean_rows: list[tuple[Any, ...]] = []
        for i in range(1, rows + 1):
            owner_id = ((i - 1) % 20) + 1
            stage = stages[(i - 1) % 3]
            amount = max(1.0, rnd.gauss(50_000.0, 2_000.0))
            email = f"user{i}@ex.com"
            website = f"https://ex{i}.com"
            clean_rows.append((i, owner_id, stage, amount, email, website))
        conn.executemany(
            "INSERT INTO deals (id, owner_id, stage, amount, email, website) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            clean_rows,
        )

        # 25 planted anomalies on distinct rows, positions seeded and spread.
        n_plant = 25
        if rows < n_plant:
            raise ValueError(f"rows must be >= {n_plant} to plant all anomaly families")
        plant_ids = sorted(rnd.sample(range(1, rows + 1), n_plant))
        # Fixed family slots over the sampled ids (counts per plan).
        slots: list[tuple[str, str, Any]] = (
            [("picklist_invalid", "stage", "zombie")] * 5
            + [("required_missing", "stage", "")] * 5
            + [("orphan_reference", "owner_id", 9999)] * 5
            + [("invalid_format", "email", "not-an-email")] * 3
            + [("invalid_format", "website", "ht!tp:/broken necklace")] * 2
            + [("stat_outlier", "amount", 5_000_000.0)] * 3
            + [("stat_outlier", "amount", 0.01)] * 2
        )
        assert len(slots) == n_plant

        conn.execute("PRAGMA ignore_check_constraints=ON")
        for rid, (family, field, value) in zip(plant_ids, slots):
            conn.execute(
                f'UPDATE deals SET "{field}" = ? WHERE id = ?',
                (value, rid),
            )
            truth.append(
                {
                    "record_id": str(rid),
                    "family": family,
                    "field": field,
                }
            )
        conn.execute("PRAGMA ignore_check_constraints=OFF")
        conn.commit()
    finally:
        conn.close()

    truth_path = out / "truth.json"
    truth_path.write_text(json.dumps(truth, indent=2), encoding="utf-8")

    return {
        "db_path": db_path,
        "table": "deals",
        "truth": truth,
        "rows_total": rows,
    }


def score(findings: list[dict], truth: list[dict]) -> dict:
    """Score engine findings against planted ground truth.

    Contract families get precision; statistical families report extra candidates
    honestly (not labelled false positives).
    """
    # Normalise ids to str for matching.
    findings_n: list[dict] = []
    for f in findings:
        findings_n.append(
            {
                **f,
                "record_id": str(f.get("record_id", "")),
                "finding_type": f.get("finding_type") or f.get("type") or "",
            }
        )

    truth_n = [
        {
            "record_id": str(t["record_id"]),
            "family": t["family"],
            "field": t.get("field"),
        }
        for t in truth
    ]

    # Pre-index findings by (record_id, type)
    findings_by_rec: dict[str, list[dict]] = {}
    for f in findings_n:
        findings_by_rec.setdefault(f["record_id"], []).append(f)

    per_family: dict[str, dict[str, Any]] = {}
    matched_truth_keys: set[tuple[str, str, str | None]] = set()
    true_positive_findings: set[int] = set()  # id(f) of matched finding dicts

    for family in FAMILY_ORDER:
        planted = [t for t in truth_n if t["family"] == family]
        allowed = FAMILY_MAP[family]
        matched = 0
        for t in planted:
            for f in findings_by_rec.get(t["record_id"], []):
                if f["finding_type"] in allowed:
                    matched += 1
                    matched_truth_keys.add((t["record_id"], family, t.get("field")))
                    true_positive_findings.add(id(f))
                    break
        total = len(planted)
        recall = (matched / total) if total else 1.0

        if family in CONTRACT_FAMILIES:
            type_findings = [f for f in findings_n if f["finding_type"] in allowed]
            # A finding is TP if its record_id is among planted truth for this family.
            planted_ids = {t["record_id"] for t in planted}
            tp = sum(1 for f in type_findings if f["record_id"] in planted_ids)
            denom = len(type_findings)
            precision = (tp / denom) if denom else 1.0
            per_family[family] = {
                "recall": recall,
                "precision": precision,
                "matched": matched,
                "planted": total,
            }
        else:
            per_family[family] = {
                "recall": recall,
                "precision": None,
                "matched": matched,
                "planted": total,
            }

    # Statistical extras: findings of outlier/ML types not matching planted stat_outlier rows.
    planted_stat_ids = {
        t["record_id"] for t in truth_n if t["family"] == "stat_outlier"
    }
    extra = 0
    for f in findings_n:
        if f["finding_type"] not in STAT_FINDING_TYPES:
            continue
        if f["record_id"] in planted_stat_ids and f["finding_type"] in FAMILY_MAP[
            "stat_outlier"
        ]:
            # Planted extreme can legitimately produce numeric and/or robust — not "extra".
            continue
        extra += 1

    return {
        "per_family": per_family,
        "extra_statistical_candidates": extra,
        "findings_total": len(findings_n),
    }


def _fmt_pct(v: float | None) -> str:
    if v is None:
        return "n/a"
    return f"{v:.3f}"


def _score_table_md(path_label: str, scored: dict) -> list[str]:
    lines = [
        f"### {path_label}",
        "",
        f"Findings total: **{scored['findings_total']}**",
        "",
        "| family | planted | matched | recall | precision |",
        "|---|---:|---:|---:|---:|",
    ]
    for family in FAMILY_ORDER:
        row = scored["per_family"][family]
        prec = row["precision"]
        lines.append(
            f"| {family} | {row['planted']} | {row['matched']} | "
            f"{_fmt_pct(row['recall'])} | {_fmt_pct(prec)} |"
        )
    lines.append("")
    lines.append(
        f"**Extra statistical candidates** (numeric_outlier / robust_outlier / ml_anomaly "
        f"beyond planted ground-truth extremes): **{scored['extra_statistical_candidates']}**."
    )
    lines.append("")
    lines.append(
        "> These are **not** labelled false positives. Gaussian clean rows legitimately have "
        "tail values; Isolation-Forest contamination flags ~2% by design. They are candidates "
        "beyond the planted ground-truth set, reported honestly as a separate bucket."
    )
    lines.append("")
    return lines


def _write_report(
    workdir: Path,
    *,
    rows: int,
    seed: int,
    mem: dict,
    stream: dict,
) -> Path:
    today = date.today().isoformat()
    lines = [
        "# Accuracy benchmark results",
        "",
        "Canonical contract-vs-behavior accuracy report for the universal data-anomaly detector.",
        "Generated by `python -m tools.benchmark` against a seeded SQLite dataset with **known** "
        "planted anomalies. The engine is exercised as a black box via "
        "`app.api.container.build_container` (no test doubles for detectors).",
        "",
        f"- **rows**: {rows}",
        f"- **seed**: {seed}",
        f"- **date**: {today}",
        "- **ground truth**: `truth.json` beside `bench.db` in the run workdir",
        "- **paths**: in-memory `run_scan` and streaming `stream_scan` (separate containers)",
        "",
        "## Scoring notes",
        "",
        "- **Contract families** (`picklist_invalid`, `required_missing`, `orphan_reference`, "
        "`invalid_format`): precision = true-positive findings / all findings of those types. "
        "Clean rows are constructed to be contract-clean, so precision is expected to be **1.0**.",
        "- **Statistical family** (`stat_outlier` → `numeric_outlier` / `robust_outlier`): "
        "recall vs planted extremes; extra flags go to the honest "
        "**extra statistical candidates** bucket (not false positives).",
        "",
        "## Results",
        "",
    ]
    lines.extend(_score_table_md("In-memory path (`run_scan`)", mem))
    lines.extend(_score_table_md("Streaming path (`stream_scan`)", stream))
    lines.extend(
        [
            "## How to reproduce",
            "",
            "```powershell",
            f"python -m tools.benchmark --rows {rows} --seed {seed} --out docs",
            "```",
            "",
            "CI enforces contract-family precision/recall via `tests/test_benchmark.py`.",
            "",
        ]
    )
    path = workdir / "benchmark-results.md"
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def _print_summary(label: str, scored: dict) -> None:
    print(f"\n=== {label} ===")
    print(f"{'family':<22} {'planted':>8} {'matched':>8} {'recall':>8} {'precision':>10}")
    for family in FAMILY_ORDER:
        row = scored["per_family"][family]
        print(
            f"{family:<22} {row['planted']:>8} {row['matched']:>8} "
            f"{_fmt_pct(row['recall']):>8} {_fmt_pct(row['precision']):>10}"
        )
    print(
        f"extra_statistical_candidates={scored['extra_statistical_candidates']}  "
        f"findings_total={scored['findings_total']}"
    )


async def run_benchmark(workdir: Path, *, rows: int = 2000, seed: int = 1234) -> dict:
    """Generate dataset, scan deals via run_scan and stream_scan, score, write report."""
    workdir = Path(workdir)
    workdir.mkdir(parents=True, exist_ok=True)

    meta = generate_dataset(workdir, rows=rows, seed=seed)
    db_path = Path(meta["db_path"])
    truth = meta["truth"]
    max_records = max(rows + 100, 10_000)

    # (a) in-memory path — separate container / data dir
    mem_data = workdir / "scanner_memory"
    mem_container = build_container(data_dir=mem_data, root=REPO_ROOT)
    mem_container.database.setup()
    mem_source = SqliteTableSource(
        db_path=db_path, table_name="deals", max_records=max_records
    )
    mem_scan = await mem_container.scan_service.run_scan(mem_source)
    mem_findings = mem_container.finding_service.list_findings(scan_id=mem_scan["id"])
    mem_score = score(mem_findings, truth)

    # (b) streaming path — separate container / data dir
    stream_data = workdir / "scanner_stream"
    stream_container = build_container(data_dir=stream_data, root=REPO_ROOT)
    stream_container.database.setup()
    stream_source = SqliteTableSource(
        db_path=db_path, table_name="deals", max_records=max_records
    )
    stream_scan = await stream_container.scan_service.stream_scan(stream_source)
    stream_findings = stream_container.finding_service.list_findings(
        scan_id=stream_scan["id"]
    )
    stream_score = score(stream_findings, truth)

    report_path = _write_report(
        workdir, rows=rows, seed=seed, mem=mem_score, stream=stream_score
    )

    return {
        "rows": rows,
        "seed": seed,
        "db_path": str(db_path),
        "truth": truth,
        "report_path": str(report_path),
        "in_memory": mem_score,
        "streaming": stream_score,
        "in_memory_scan_id": mem_scan["id"],
        "streaming_scan_id": stream_scan["id"],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Accuracy benchmark: plant anomalies, scan, score, report."
    )
    parser.add_argument("--rows", type=int, default=2000, help="Clean row count (default 2000)")
    parser.add_argument("--seed", type=int, default=1234, help="RNG seed (default 1234)")
    parser.add_argument(
        "--out",
        type=str,
        default="docs",
        help="Output directory for bench.db, truth.json, benchmark-results.md",
    )
    args = parser.parse_args(argv)

    out = Path(args.out)
    if not out.is_absolute():
        out = REPO_ROOT / out

    result = asyncio.run(run_benchmark(out, rows=args.rows, seed=args.seed))
    _print_summary("In-memory (run_scan)", result["in_memory"])
    _print_summary("Streaming (stream_scan)", result["streaming"])
    print(f"\nReport: {result['report_path']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
