from __future__ import annotations

from datetime import UTC, datetime, timedelta

from ..domain.models import stable_hash
from ..persistence.database import Database, now
from ..persistence.repositories import AuditRepository, FindingRepository, SyncRepository
from ..persistence.source_key import source_key_from_name
from .scan_service import ScanService


def _derive_source_key(source) -> str:
    """Compute source_key for a not-yet-loaded source, mirroring the source_name formula each
    ingestion class uses internally (so it matches what scan_service will independently compute
    from the loaded dataset). Needed here because watermark lookup happens before source.load()."""
    source_name = getattr(source, "source_name", None)
    if source_name is None:
        host = (source.session.instance_url or "").split("//", 1)[-1]
        source_name = f"salesforce:{host}/{source.object_name}"
    return source_key_from_name(source_name)


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

    def _ledger_flush(
        self, *, object_name: str, entries: list[tuple[str, str]], seen_at: str, source_key: str
    ) -> None:
        """Flush ledger entries on their own short-lived connection (bounded memory per batch)."""
        if not entries:
            return
        with self.database.connect() as conn:
            self.sync.ledger_upsert_many(
                conn, object_name=object_name, entries=entries, seen_at=seen_at, source_key=source_key
            )

    async def incremental_scan(self, source, *, overlap_minutes: int = 5) -> dict:
        self.database.setup()
        object_name = source.object_name
        src_key = _derive_source_key(source)
        with self.database.connect() as conn:
            watermark = self.sync.get_watermark(conn, object_name, src_key)
        if watermark is not None:
            watermark_dt = datetime.fromisoformat(watermark.replace("Z", "+00:00")).astimezone(UTC)
            since = watermark_dt - timedelta(minutes=overlap_minutes)
            source.modified_since = since.strftime("%Y-%m-%dT%H:%M:%SZ")

        streaming = hasattr(source, "aiter_batches")
        seen_at = now()

        if streaming:
            original_aiter_batches = source.aiter_batches
            changed_records = 0

            async def _tee(batch_size=None):
                nonlocal changed_records
                async for batch in original_aiter_batches(batch_size):
                    entries = [(r["Id"], stable_hash(r)) for r in batch if r.get("Id")]
                    self._ledger_flush(object_name=object_name, entries=entries, seen_at=seen_at, source_key=src_key)
                    changed_records += len(batch)
                    yield batch

            source.aiter_batches = _tee
            scan = await self.scan_service.stream_scan(source)
        else:
            dataset = await source.load()
            scan = await self.scan_service.run_scan(_Preloaded(dataset))
            changed_records = len(dataset.records)
            entries = [(record["Id"], stable_hash(record)) for record in dataset.records if record.get("Id")]
            self._ledger_flush(object_name=object_name, entries=entries, seen_at=seen_at, source_key=src_key)

        mode = "incremental" if watermark else "initial"
        if streaming:
            mode = mode + "-bulk"

        with self.database.connect() as conn:
            self.sync.set_watermark(conn, object_name, seen_at, src_key)
            self.audit.append(
                conn,
                event="incremental_scan",
                actor="system",
                scan_id=scan["id"],
                object_name=object_name,
                detail={
                    "object_name": object_name,
                    "mode": mode,
                    "changed_records": changed_records,
                    "watermark_overlap_minutes": overlap_minutes,
                },
            )
        scan["sync_mode"] = mode
        scan["changed_records"] = changed_records
        return scan

    async def reconcile(self, source) -> dict:
        self.database.setup()
        object_name = source.object_name
        src_key = _derive_source_key(source)
        source.modified_since = None
        streaming = hasattr(source, "aiter_batches")
        seen_at = now()

        if streaming:
            original_aiter_batches = source.aiter_batches
            current: set[str] = set()
            record_count = 0

            async def _tee(batch_size=None):
                nonlocal record_count
                async for batch in original_aiter_batches(batch_size):
                    entries = [(r["Id"], stable_hash(r)) for r in batch if r.get("Id")]
                    self._ledger_flush(object_name=object_name, entries=entries, seen_at=seen_at, source_key=src_key)
                    current.update(rid for rid, _ in entries)
                    record_count += len(batch)
                    yield batch

            source.aiter_batches = _tee
            scan = await self.scan_service.stream_scan(source)
        else:
            dataset = await source.load()
            scan = await self.scan_service.run_scan(_Preloaded(dataset))
            current = {record["Id"] for record in dataset.records if record.get("Id")}
            entries = [(record_id, stable_hash(record)) for record in dataset.records if (record_id := record.get("Id"))]
            record_count = len(dataset.records)

        with self.database.connect() as conn:
            known = self.sync.ledger_active_ids(conn, object_name, src_key)
            vanished = sorted(known - current)
            deleted = self.sync.ledger_mark_deleted(conn, object_name, vanished, src_key)
            resolved = self.findings.resolve_for_records(
                conn, object_name=object_name, record_ids=vanished, source_key=src_key
            )
            if not streaming:
                self.sync.ledger_upsert_many(
                    conn, object_name=object_name, entries=entries, seen_at=seen_at, source_key=src_key
                )
            self.sync.set_watermark(conn, object_name, seen_at, src_key)
            self.audit.append(
                conn,
                event="reconciliation",
                actor="system",
                scan_id=scan["id"],
                object_name=object_name,
                detail={
                    "object_name": object_name,
                    "records": record_count,
                    "vanished": deleted,
                    "resolved_findings": resolved,
                },
            )
        scan["vanished_records"] = deleted
        scan["resolved_findings"] = resolved
        return scan
