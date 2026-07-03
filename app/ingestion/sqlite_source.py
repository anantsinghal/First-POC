from __future__ import annotations

import re
import sqlite3
from pathlib import Path
from typing import Any

from ..domain.models import Record, stable_hash
from ..domain.schema import FieldDescriptor, FieldType, ObjectSchema
from ..domain.values import clean
from .base import DataSource, IngestionError, LoadedDataset


TABLE_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class SqliteTableSource(DataSource):
    """SQLite table as DataSource adapter. Read-only, translates PRAGMA + sqlite_master to canonical ObjectSchema."""

    def __init__(
        self,
        *,
        db_path: str | Path,
        table_name: str,
        max_records: int = 10_000,
        resolve_references: bool = True,
    ) -> None:
        self.db_path = Path(db_path)
        self.table_name = table_name
        self.max_records = max_records
        self.resolve_references = resolve_references

    def _validate_table_name(self, name: str) -> None:
        if not TABLE_NAME_RE.fullmatch(name):
            raise IngestionError("Invalid table name", status_code=400)

    async def load(self) -> LoadedDataset:
        self._validate_table_name(self.table_name)
        db_uri = f"file:{self.db_path}?mode=ro"
        conn: sqlite3.Connection | None = None
        try:
            conn = sqlite3.connect(db_uri, uri=True)
            # check table exists
            cur = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name=?", (self.table_name,)
            )
            if cur.fetchone() is None:
                raise IngestionError(f"Table '{self.table_name}' not found", status_code=400)

            schema = self._build_schema(conn)

            # fetch data with limit +1 for truncation
            limit = self.max_records + 1
            pragma = conn.execute(f'PRAGMA table_info("{self.table_name}")').fetchall()
            col_names = [row[1] for row in pragma]
            select_cols = ", ".join(f'"{c}"' for c in col_names)
            sql = f'SELECT {select_cols}, rowid FROM "{self.table_name}" LIMIT ?'
            rows = conn.execute(sql, (limit,)).fetchall()

            truncated = len(rows) > self.max_records
            rows = rows[: self.max_records]

            # pk col
            pk_col = None
            for row in pragma:
                if row[5] == 1:
                    pk_col = row[1]
                    break

            records: list[Record] = []
            orig_cols = [c for c in col_names]
            fields = ["Id"] + [c for c in orig_cols if c != "Id"]

            for r in rows:
                rec: Record = {}
                for i, c in enumerate(col_names):
                    v = r[i]
                    rec[c] = clean(str(v)) if v is not None else ""
                if pk_col and pk_col in rec:
                    rec["Id"] = rec[pk_col]
                else:
                    rowid_val = r[-1]
                    rec["Id"] = clean(str(rowid_val)) if rowid_val is not None else ""
                for c in fields:
                    if c not in rec:
                        rec[c] = ""
                records.append(rec)

            # related_ids
            related_ids: dict[str, frozenset[str]] = {}
            if self.resolve_references:
                for fd in schema.reference_fields():
                    if len(fd.reference_to) == 1:
                        parent = fd.reference_to[0]
                        self._validate_table_name(parent)
                        try:
                            p_prag = conn.execute(f'PRAGMA table_info("{parent}")').fetchall()
                            p_pk = None
                            for prow in p_prag:
                                if prow[5] == 1:
                                    p_pk = prow[1]
                                    break
                            if p_pk:
                                pcur = conn.execute(f'SELECT "{p_pk}" FROM "{parent}" LIMIT 100000')
                            else:
                                pcur = conn.execute(f'SELECT rowid FROM "{parent}" LIMIT 100000')
                            pids = {
                                clean(str(x[0]))
                                for x in pcur.fetchall()
                                if x[0] is not None and clean(str(x[0]))
                            }
                            if pids:
                                related_ids[parent] = pids
                        except Exception:
                            pass

            source_name = f"sqlite:{self.db_path.name}/{self.table_name}"
            source_hash = stable_hash({"fields": fields, "records": records})

            return LoadedDataset(
                object_name=self.table_name,
                source_name=source_name,
                fields=fields,
                records=records,
                source_hash=source_hash,
                schema=schema,
                related_ids=related_ids or None,
                truncated=truncated,
            )
        finally:
            if conn is not None:
                try:
                    conn.close()
                except Exception:
                    pass

    def _build_schema(self, conn: sqlite3.Connection) -> ObjectSchema:
        table = self.table_name
        pragma = conn.execute(f'PRAGMA table_info("{table}")').fetchall()
        fields: dict[str, FieldDescriptor] = {}
        pk_col = None
        for row in pragma:
            name = row[1]
            decl_type = (row[2] or "").upper()
            notnull = row[3]
            pk = row[5]
            if pk == 1:
                pk_col = name
            if any(k in decl_type for k in ("INT", "REAL", "FLOA", "DOUB", "NUMER", "DEC")):
                data_type = FieldType.NUMBER
            elif "BOOL" in decl_type:
                data_type = FieldType.BOOLEAN
            elif "DATE" in decl_type or "TIME" in decl_type:
                data_type = FieldType.DATE
            else:
                data_type = FieldType.STRING
            required = (notnull == 1 and pk == 0)
            fd = FieldDescriptor(name=name, data_type=data_type, required=required)
            fields[name] = fd

        if pk_col:
            if pk_col != "Id":
                if "Id" not in fields:
                    fields["Id"] = FieldDescriptor(name="Id", data_type=FieldType.ID)
            else:
                fields["Id"] = FieldDescriptor(name="Id", data_type=FieldType.ID, required=False)
        else:
            if "Id" not in fields:
                fields["Id"] = FieldDescriptor(name="Id", data_type=FieldType.ID)

        # fks
        fk_list = conn.execute(f'PRAGMA foreign_key_list("{table}")').fetchall()
        for fk in fk_list:
            parent = fk[2]
            from_col = fk[3]
            self._validate_table_name(parent)
            if from_col in fields:
                fd = fields[from_col]
                fd = FieldDescriptor(
                    name=fd.name,
                    data_type=FieldType.REFERENCE,
                    required=fd.required,
                    reference_to=(parent,),
                )
                fields[from_col] = fd

        # CHECK
        cur = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (table,)
        )
        create_sql_row = cur.fetchone()
        if create_sql_row and create_sql_row[0]:
            sql = create_sql_row[0]
            for m in re.finditer(r'(?i)CHECK\s*\(\s*"?(\w+)"?\s+IN\s*\(([^)]+)\)\s*\)', sql):
                col = m.group(1)
                vals_str = m.group(2)
                vals = [v.strip().strip("'\"") for v in vals_str.split(",") if v.strip()]
                if col in fields:
                    fd = fields[col]
                    if fd.data_type != FieldType.REFERENCE and col != pk_col:
                        fd = FieldDescriptor(
                            name=fd.name,
                            data_type=FieldType.PICKLIST,
                            required=fd.required,
                            picklist_values=frozenset(vals),
                            reference_to=fd.reference_to,
                        )
                        fields[col] = fd

        if "Id" not in fields:
            fields["Id"] = FieldDescriptor(name="Id", data_type=FieldType.ID)

        return ObjectSchema(object_name=table, fields=fields)
