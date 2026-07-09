"""Scheduled scanning CLI for Task Scheduler / cron.

Builds a container against SCANNER_DATA_DIR and runs one scan mode.
Prints a single final status line and exits 0/1.

Run: ``python -m tools.scheduled_scan table --db path --table deals``
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

from app.api.container import build_container
from app.ingestion.salesforce_source import SalesforceObjectSource
from app.ingestion.sqlite_source import SqliteTableSource
from app.salesforce import SalesforceClient, session_from_environment

REPO_ROOT = Path(__file__).resolve().parents[1]


def _data_dir() -> Path:
    raw = os.environ.get("SCANNER_DATA_DIR", "./data")
    p = Path(raw)
    if not p.is_absolute():
        p = (Path.cwd() / p).resolve()
    return p


def _build_container():
    data_dir = _data_dir()
    data_dir.mkdir(parents=True, exist_ok=True)
    container = build_container(data_dir=data_dir, root=REPO_ROOT)
    container.database.setup()
    return container


def _emit(status: str, mode: str, findings: int) -> None:
    print(f"SCHEDULED_SCAN status={status} mode={mode} findings={findings}")


async def _cmd_org(args: argparse.Namespace) -> int:
    container = _build_container()
    session = await session_from_environment()
    client = SalesforceClient(session)
    kwargs: dict = {}
    if args.max_objects is not None:
        kwargs["max_objects"] = args.max_objects
    if args.stream_threshold is not None:
        kwargs["stream_threshold"] = args.stream_threshold
    if args.api_budget is not None:
        kwargs["api_budget"] = args.api_budget
    result = await container.orchestrator_service.scan_org(
        client=client, session=session, **kwargs
    )
    findings = int(result.get("total_findings") or 0)
    print(
        f"org summary: discovered={result.get('objects_discovered')} "
        f"scanned={len(result.get('scanned') or [])} "
        f"skipped={len(result.get('skipped') or [])} "
        f"failed={len(result.get('failed') or [])} "
        f"findings={findings}"
    )
    _emit("ok", "org", findings)
    return 0


async def _cmd_object(args: argparse.Namespace) -> int:
    container = _build_container()
    session = await session_from_environment()
    client = SalesforceClient(session)
    max_records = args.max_records if args.max_records is not None else 500
    source = SalesforceObjectSource(
        client=client,
        session=session,
        object_name=args.name,
        max_records=max_records,
    )
    if args.incremental:
        scan = await container.sync_service.incremental_scan(source)
        mode = "object-incremental"
    else:
        scan = await container.scan_service.run_scan(source)
        mode = "object"
    findings = int(scan.get("finding_count") or 0)
    print(
        f"object summary: name={args.name} scan_id={scan.get('id')} "
        f"rows={scan.get('row_count')} findings={findings}"
    )
    _emit("ok", mode, findings)
    return 0


async def _cmd_table(args: argparse.Namespace) -> int:
    container = _build_container()
    max_records = 10_000_000
    source = SqliteTableSource(
        db_path=args.db,
        table_name=args.table,
        max_records=max_records,
    )
    if args.stream:
        scan = await container.scan_service.stream_scan(source)
        mode = "table-stream"
    else:
        scan = await container.scan_service.run_scan(source)
        mode = "table"
    findings = int(scan.get("finding_count") or 0)
    print(
        f"table summary: db={args.db} table={args.table} "
        f"scan_id={scan.get('id')} rows={scan.get('row_count')} findings={findings}"
    )
    _emit("ok", mode, findings)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Scheduled scan runner (Task Scheduler / cron friendly)."
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_org = sub.add_parser("org", help="Org-wide Salesforce scan via orchestrator")
    p_org.add_argument("--max-objects", type=int, default=None)
    p_org.add_argument("--stream-threshold", type=int, default=None)
    p_org.add_argument("--api-budget", type=int, default=None)

    p_obj = sub.add_parser("object", help="Single Salesforce object scan")
    p_obj.add_argument("--name", required=True, help="Salesforce object API name")
    p_obj.add_argument("--max-records", type=int, default=None)
    p_obj.add_argument(
        "--incremental",
        action="store_true",
        help="Route through sync_service.incremental_scan",
    )

    p_table = sub.add_parser("table", help="SQLite table scan")
    p_table.add_argument("--db", required=True, help="Path to SQLite database")
    p_table.add_argument("--table", required=True, help="Table name")
    p_table.add_argument(
        "--stream",
        action="store_true",
        help="Use stream_scan instead of run_scan",
    )

    args = parser.parse_args(argv)
    try:
        if args.command == "org":
            return asyncio.run(_cmd_org(args))
        if args.command == "object":
            return asyncio.run(_cmd_object(args))
        if args.command == "table":
            return asyncio.run(_cmd_table(args))
        print(f"Unknown command: {args.command}", file=sys.stderr)
        _emit("error", str(args.command), 0)
        return 1
    except Exception as exc:
        print(f"scheduled_scan error: {exc}", file=sys.stderr)
        mode = getattr(args, "command", "unknown") or "unknown"
        if mode == "object" and getattr(args, "incremental", False):
            mode = "object-incremental"
        elif mode == "table" and getattr(args, "stream", False):
            mode = "table-stream"
        _emit("error", mode, 0)
        return 1


if __name__ == "__main__":
    sys.exit(main())
