from __future__ import annotations

from ..domain.models import Record, stable_hash
from ..domain.values import clean
from ..salesforce.client import SalesforceClient
from ..salesforce.session import SalesforceSession
from .base import DataSource, IngestionError, LoadedDataset


DEFAULT_SF_POLICY_FIELDS: list[str] = [
    "Id", "Name", "StageName", "Amount", "AccountId",
    "Contract__c", "Segment__c", "CloseDate",
]
"""Default policy fields used in baseline scan_salesforce (Id must be present for trustworthy scan)."""


class SalesforceObjectSource(DataSource):
    """Salesforce object source implementing DataSource via REST describe + query."""

    def __init__(
        self,
        *,
        client: SalesforceClient,
        session: SalesforceSession,
        object_name: str,
        max_records: int = 500,
        policy_fields: list[str] = DEFAULT_SF_POLICY_FIELDS,
    ) -> None:
        """Store client/session and params; load() performs describe/query + cleaning."""
        self.client = client
        self.session = session
        self.object_name = object_name
        self.max_records = max_records
        self.policy_fields = policy_fields

    async def load(self) -> LoadedDataset:
        """Describe, filter fields, query, clean records to strings, compute deterministic source_hash via stable_hash."""
        describe = await self.client.describe_sobject(self.object_name)
        readable = {
            f.get("name")
            for f in describe.get("fields", [])
            if f.get("name") and f.get("queryable")
        }
        fields = [f for f in self.policy_fields if f in readable]
        if "Id" not in fields:
            raise IngestionError(
                "The integration user cannot query Id for this object; no trustworthy scan is possible.",
                status_code=422,
            )
        records = await self.client.query_records(
            f"SELECT {', '.join(fields)} FROM {self.object_name}",
            max_records=self.max_records,
        )
        cleaned: list[Record] = [
            {f: clean(str(r[f])) if r.get(f) is not None else "" for f in fields}
            for r in records
        ]
        host = self.session.instance_url.split("//", 1)[-1]
        source_name = f"salesforce:{host}/{self.object_name}"
        source_hash = stable_hash({"fields": fields, "records": cleaned})
        return LoadedDataset(
            object_name=self.object_name,
            source_name=source_name,
            fields=fields,
            records=cleaned,
            source_hash=source_hash,
        )
