from __future__ import annotations

from ..ingestion.salesforce_bulk_source import SalesforceBulkSource
from ..ingestion.salesforce_source import SalesforceObjectSource
from ..persistence.database import Database
from ..persistence.repositories import AuditRepository
from ..salesforce.client import SalesforceClient
from ..salesforce.session import SalesforceSession
from .scan_service import ScanService


class OrgScanOrchestrator:
    """Discovers every queryable object in an org, sizes + routes each object to an in-memory or
    bulk-stream scan, respects an API budget, and reports an honest scanned/skipped/failed summary.
    """

    SYSTEM_OBJECT_SUFFIXES = ("ChangeEvent", "Feed", "History", "Share", "Tag")
    """Generic Salesforce system child-object suffixes, skipped org-agnostically (suffix, not name)."""

    def __init__(self, *, scan_service: ScanService, audit: AuditRepository, database: Database) -> None:
        self.scan_service = scan_service
        self.audit = audit
        self.database = database

    async def scan_org(
        self,
        *,
        client: SalesforceClient,
        session: SalesforceSession,
        include: list[str] | None = None,
        exclude: list[str] | None = None,
        max_objects: int | None = None,
        stream_threshold: int = 10_000,
        small_max_records: int = 2_000,
        bulk_max_records: int = 1_000_000,
        api_budget: int | None = None,
    ) -> dict:
        self.database.setup()
        scanned: list[dict] = []
        skipped: list[dict] = []
        failed: list[dict] = []
        total_findings = 0
        api_calls_used = 0

        describe = await client.describe_global()
        api_calls_used += 1
        sobjects = list(describe.get("sobjects") or [])
        objects_discovered = len(sobjects)

        include_set = set(include) if include else None
        exclude_set = set(exclude or [])

        candidates: list[dict] = []
        for sob in sobjects:
            name = sob.get("name")
            if not name:
                continue
            if not sob.get("queryable") or sob.get("deprecatedAndHidden"):
                skipped.append({"object": name, "reason": "not queryable"})
                continue
            if name.endswith(self.SYSTEM_OBJECT_SUFFIXES):
                skipped.append({"object": name, "reason": "system child object"})
                continue
            if name in exclude_set:
                skipped.append({"object": name, "reason": "excluded"})
                continue
            if include_set is not None and name not in include_set:
                continue
            candidates.append(sob)

        candidates.sort(key=lambda s: (not s.get("custom"), s.get("name")))

        if max_objects is not None and len(candidates) > max_objects:
            overflow, candidates = candidates[max_objects:], candidates[:max_objects]
            for sob in overflow:
                skipped.append({"object": sob.get("name"), "reason": "max_objects cap"})

        budget_remaining = api_budget

        for sob in candidates:
            name = sob["name"]
            if budget_remaining is not None and budget_remaining <= 0:
                skipped.append({"object": name, "reason": "api budget exhausted"})
                continue
            try:
                count_payload = await client.json(
                    "GET", "/query", params={"q": f"SELECT COUNT() FROM {name}"}
                )
                api_calls_used += 1
                if budget_remaining is not None:
                    budget_remaining -= 1
                count = count_payload.get("totalSize", 0)
                if not count:
                    skipped.append({"object": name, "reason": "no records"})
                    continue
                if count <= stream_threshold:
                    route = "memory"
                    source = SalesforceObjectSource(
                        client=client,
                        session=session,
                        object_name=name,
                        max_records=small_max_records,
                    )
                    scan = await self.scan_service.run_scan(source)
                else:
                    route = "bulk-stream"
                    source = SalesforceBulkSource(
                        client=client,
                        session=session,
                        object_name=name,
                        max_records=bulk_max_records,
                    )
                    scan = await self.scan_service.stream_scan(source)
                api_calls_used += 2
                if budget_remaining is not None:
                    budget_remaining -= 2
                finding_count = scan.get("finding_count", 0)
                scanned.append(
                    {
                        "object": name,
                        "scan_id": scan["id"],
                        "route": route,
                        "rows": scan.get("row_count", 0),
                        "findings": finding_count,
                        "coverage_grade": (scan.get("coverage") or {}).get("grade"),
                    }
                )
                total_findings += finding_count
            except Exception as exc:
                failed.append({"object": name, "error": str(exc)})

        with self.database.connect() as conn:
            self.audit.append(
                conn,
                event="org_scan",
                actor="system",
                detail={
                    "objects_discovered": objects_discovered,
                    "scanned": len(scanned),
                    "skipped": len(skipped),
                    "failed": len(failed),
                    "total_findings": total_findings,
                    "api_calls_used": api_calls_used,
                },
            )

        return {
            "objects_discovered": objects_discovered,
            "scanned": scanned,
            "skipped": skipped,
            "failed": failed,
            "total_findings": total_findings,
            "api_calls_used": api_calls_used,
        }
