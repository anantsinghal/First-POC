from __future__ import annotations

import asyncio
import csv
import io
from collections.abc import AsyncIterator

from ..domain.models import Record, stable_hash
from ..domain.values import clean
from ..salesforce.client import SalesforceClient
from ..salesforce.session import SalesforceError, SalesforceSession
from .base import DataSource, IngestionError, LoadedDataset
from .salesforce_schema import schema_from_salesforce_describe
from .salesforce_source import EXCLUDED_FIELD_TYPES, MAX_QUERY_FIELDS

from app.salesforce import SAFE_API_NAME


class SalesforceBulkSource(DataSource):
    """Salesforce Bulk API 2.0 streaming source for large extracts (mock-tested, no real creds)."""

    def __init__(
        self,
        *,
        client: SalesforceClient,
        session: SalesforceSession,
        object_name: str,
        max_records: int = 1_000_000,
        poll_interval: float = 2.0,
        max_polls: int = 60,
        batch_size: int = 5_000,
        fields: list[str] | None = None,
    ) -> None:
        if not SAFE_API_NAME.fullmatch(object_name):
            raise IngestionError("Invalid Salesforce object API name.", status_code=400)
        self.client = client
        self.session = session
        self.object_name = object_name
        self.max_records = max_records
        self.poll_interval = poll_interval
        self.max_polls = max_polls
        self.batch_size = batch_size
        self.fields = fields
        host = (session.instance_url or "").split("//", 1)[-1].rstrip("/")
        self.source_name = f"salesforce-bulk:{host}/{object_name}"
        self.fieldnames: list[str] = []
        self.schema = None
        self.truncated = False
        self.related_ids = None
        self._job_id: str | None = None

    async def _prepare(self) -> None:
        """Describe-driven selection (reusing constants + schema builder), create job, poll to JobComplete."""
        describe = await self.client.describe_sobject(self.object_name)
        self.schema = schema_from_salesforce_describe(describe)
        readable = {
            f.get("name")
            for f in describe.get("fields", [])
            if f.get("name") and f.get("queryable")
        }
        if self.fields is not None:
            selected = [f for f in self.fields if f in readable]
        else:
            selected = [
                f["name"]
                for f in describe.get("fields", [])
                if f.get("queryable")
                and f.get("name")
                and (f.get("type") or "").lower() not in EXCLUDED_FIELD_TYPES
                and "." not in f["name"]
            ]
        if "Id" not in selected:
            raise IngestionError(
                "The integration user cannot query Id for this object; no trustworthy scan is possible.",
                status_code=422,
            )
        fields = ["Id", *[f for f in selected if f != "Id"]]
        if len(fields) > MAX_QUERY_FIELDS:
            fields = fields[:MAX_QUERY_FIELDS]
        self.fieldnames = fields
        soql = f"SELECT {', '.join(fields)} FROM {self.object_name}"
        job_id = await self.client.bulk_create_query_job(soql)
        self._job_id = job_id
        last_state = "Unknown"
        for _ in range(self.max_polls):
            state = await self.client.bulk_job_status(job_id)
            last_state = state
            if state == "JobComplete":
                return
            await asyncio.sleep(self.poll_interval)
        raise IngestionError(f"Bulk job did not complete (last state: {last_state})", status_code=502)

    async def aiter_batches(self, batch_size: int | None = None) -> AsyncIterator[list[Record]]:
        """Async generator yielding cleaned record batches from Bulk CSV pages.

        Each page (with its own header) is parsed; respects max_records; sets truncated/fieldnames/schema.
        """
        bs = batch_size if batch_size is not None else self.batch_size
        if self._job_id is None or not self.fieldnames:
            await self._prepare()
        batch: list[Record] = []
        seen = 0
        self.truncated = False
        if self._job_id is None:
            return
        async for page in self.client.bulk_job_results_pages(self._job_id, max_records=self.max_records):
            if not page or not page.strip():
                continue
            reader = csv.DictReader(io.StringIO(page))
            for row in reader:
                if seen >= self.max_records:
                    self.truncated = True
                    break
                rec: Record = {}
                for f in self.fieldnames:
                    v = row.get(f)
                    rec[f] = clean(v) if v is not None else ""
                batch.append(rec)
                seen += 1
                if len(batch) >= bs:
                    yield batch
                    batch = []
            if self.truncated:
                break
        if batch:
            yield batch
        # ensure attrs
        if not self.fieldnames:
            self.fieldnames = self.fieldnames or []
        self.truncated = seen >= self.max_records or self.truncated

    async def load(self) -> LoadedDataset:
        """Materialise via aiter_batches (compat with run_scan path)."""
        records: list[Record] = []
        async for b in self.aiter_batches(self.batch_size):
            records.extend(b)
            if len(records) >= self.max_records:
                break
        fields = self.fieldnames or []
        schema = self.schema
        truncated = self.truncated
        source_hash = stable_hash({"fields": fields, "records": records})
        return LoadedDataset(
            object_name=self.object_name,
            source_name=self.source_name,
            fields=fields,
            records=records,
            source_hash=source_hash,
            schema=schema,
            related_ids=None,
            truncated=truncated,
        )
