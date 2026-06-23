from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol, runtime_checkable

from ..domain.models import Record

if TYPE_CHECKING:
    from ..domain.schema import ObjectSchema


@dataclass(frozen=True)
class LoadedDataset:
    """Result of a data source load: object, fields, records, and content hash for idempotency."""

    object_name: str
    source_name: str
    fields: list[str]
    records: list[Record]
    source_hash: str
    schema: "ObjectSchema | None" = None
    related_ids: dict[str, frozenset[str]] | None = None
    related_records: dict[str, list[Record]] | None = None


class IngestionError(RuntimeError):
    """Error during data loading (e.g. parse failure or Id not queryable); carries suggested HTTP status."""

    def __init__(self, message: str, status_code: int = 422) -> None:
        self.status_code = status_code
        super().__init__(message)


@runtime_checkable
class DataSource(Protocol):
    """Async protocol for data sources (CSV or Salesforce); load returns a LoadedDataset."""

    async def load(self) -> LoadedDataset:
        """Load and return the dataset (CSV impl does sync work inside async)."""
        ...
