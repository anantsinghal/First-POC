from __future__ import annotations

from datetime import UTC, datetime, timedelta

from ..domain.models import stable_hash
from ..persistence.database import Database, now
from ..persistence.repositories import AuditRepository, FindingRepository, SyncRepository
from .scan_service import ScanService


class _Preloaded:
    """Tiny wrapper that lets ScanService consume a previously loaded dataset."""

    def __init__(self, dataset) -> None:
        self._dataset = dataset

    async def load(self):
        return self._dataset


class SyncService:
    """Incremental sync orchestration built on top of the existing scan pipeline."""

    def __init__(
        self,
        *,
        database: Database,
        scan_service: ScanService,
        sync: SyncRepository,
        findings: FindingRepository,
        audit: AuditRepository,
    ) -> None:
        self.database = database
        self.scan_service = scan_service
        self.sync = sync
        self.findings = findings
        self.audit = audit

    async def incremental_scan(self, source, *, overlap_minutes: int = 5) -> dict:
        self.database.setup()
        object_name = source.object_name
        with self.database.connect() as conn:
            watermark = self.sync.get_watermark(conn, object_name)
        if watermark is not None:
            watermark_dt = datetime.fromisoformat(watermark.replace("Z", "+00:00")).astimezone(UTC)
            since = watermark_dt - timedelta(minutes=overlap_minutes)
            source.modified_since = since.strftime("%Y-%m-%dT%H:%M:%SZ")
        dataset = await source.load()
        scan = await self.scan_service.run_scan(_Preloaded(dataset))
        seen_at = now()
        entries = [(record["Id"], stable_hash(record)) for record in dataset.records if record.get("Id")]
        with self.database.connect() as conn:
            self.sync.ledger_upsert_many(conn, object_name=object_name, entries=entries, seen_at=seen_at)
            self.sync.set_watermark(conn, object_name, seen_at)
            self.audit.append(
                conn,
                event="incremental_scan",
                actor="system",
                scan_id=scan["id"],
                object_name=object_name,
                detail={
                    "object_name": object_name,
                    "mode": "incremental" if watermark else "initial",
                    "changed_records": len(dataset.records),
                    "watermark_overlap_minutes": overlap_minutes,
                },
            )
        scan["sync_mode"] = "incremental" if watermark else "initial"
        scan["changed_records"] = len(dataset.records)
        return scan

    async def reconcile(self, source) -> dict:
        self.database.setup()
        object_name = source.object_name
        source.modified_since = None
        dataset = await source.load()
        scan = await self.scan_service.run_scan(_Preloaded(dataset))
        current = {record["Id"] for record in dataset.records if record.get("Id")}
        entries = [(record_id, stable_hash(record)) for record in dataset.records if (record_id := record.get("Id"))]
        seen_at = now()
        with self.database.connect() as conn:
            known = self.sync.ledger_active_ids(conn, object_name)
            vanished = sorted(known - current)
            deleted = self.sync.ledger_mark_deleted(conn, object_name, vanished)
            resolved = self.findings.resolve_for_records(
                conn, object_name=object_name, record_ids=vanished
            )
            self.sync.ledger_upsert_many(conn, object_name=object_name, entries=entries, seen_at=seen_at)
            self.sync.set_watermark(conn, object_name, seen_at)
            self.audit.append(
                conn,
                event="reconciliation",
                actor="system",
                scan_id=scan["id"],
                object_name=object_name,
                detail={
                    "object_name": object_name,
                    "records": len(current),
                    "vanished": deleted,
                    "resolved_findings": resolved,
                },
            )
        scan["vanished_records"] = deleted
        scan["resolved_findings"] = resolved
        return scan
