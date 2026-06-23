from __future__ import annotations

from ..domain.models import Record, stable_hash
from ..domain.schema import schema_from_salesforce_describe
from ..domain.values import clean
from ..salesforce.client import SalesforceClient
from ..salesforce.session import SalesforceSession
from .base import DataSource, IngestionError, LoadedDataset


EXCLUDED_FIELD_TYPES = {
    "address",
    "location",
    "base64",
    "encryptedstring",
    "textarea",
    "complexvalue",
    "anytype",
}
"""Salesforce field types skipped by default for PII minimization and query safety."""

MAX_QUERY_FIELDS = 100
"""Maximum number of fields selected from describe for a default Salesforce scan."""


class SalesforceObjectSource(DataSource):
    """Salesforce object source implementing DataSource via REST describe + query."""

    def __init__(
        self,
        *,
        client: SalesforceClient,
        session: SalesforceSession,
        object_name: str,
        max_records: int = 500,
        fields: list[str] | None = None,
    ) -> None:
        """Store client/session and params; load() performs describe/query + cleaning."""
        self.client = client
        self.session = session
        self.object_name = object_name
        self.max_records = max_records
        self.fields = fields

    async def load(self) -> LoadedDataset:
        """Describe, filter fields, query, clean records to strings, compute deterministic source_hash via stable_hash."""
        describe = await self.client.describe_sobject(self.object_name)
        schema = schema_from_salesforce_describe(describe)
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
            # POC cap: keep Id plus the first describe-ordered fields to avoid overly wide SOQL.
            fields = fields[:MAX_QUERY_FIELDS]
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
            schema=schema,
        )
