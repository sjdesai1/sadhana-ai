"""Full-refresh sync of one office into its schema, logged in sadhana_meta.sync_runs.

Every table is truncated and reloaded with Postgres COPY inside one transaction, so readers see either the
old data or the new, never half of each. Incremental sync comes with the first real PMS.
"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime, time
from decimal import Decimal
from typing import Any

from sqlalchemy import JSON, Boolean, Column, Date, Engine, Integer, Numeric, Time, insert, update
from sqlalchemy.dialects.postgresql import insert as pg_insert

from sadhana.data.db import setup_office
from sadhana.data.schema import OFFICE_TABLES, offices, sync_runs
from sadhana.sync.adapters import Adapter


def _convert(col: Column, value: Any) -> Any:
    """Source values (often strings from a file) to the column's type; empty means NULL."""
    if value is None or value == "":
        return None
    t = col.type
    if isinstance(t, JSON):
        return json.dumps(value)
    if not isinstance(value, str):
        return value
    if isinstance(t, Boolean):
        return value.strip().lower() in ("true", "1", "t", "yes")
    if isinstance(t, Date):
        return date.fromisoformat(value)
    if isinstance(t, Time):
        return time.fromisoformat(value)
    if isinstance(t, Integer):
        return int(float(value))
    if isinstance(t, Numeric):
        return Decimal(value)
    return value


def _loaded_columns(table) -> list[Column]:
    return [c for c in table.columns if not (c.primary_key and c.autoincrement is True)]


def sync_office(engine: Engine, adapter: Adapter) -> dict[str, int]:
    office_id = adapter.office_id
    with engine.begin() as conn:
        schema = setup_office(conn, office_id)
        run_id = conn.execute(
            insert(sync_runs)
            .values(office_id=office_id, source=adapter.source, status="running")
            .returning(sync_runs.c.run_id)
        ).scalar_one()
    counts: dict[str, int] = {}
    try:
        with engine.begin() as conn:
            raw = conn.connection.driver_connection
            with raw.cursor() as cur:  # type: ignore[union-attr]
                for table in OFFICE_TABLES:
                    cols = _loaded_columns(table)
                    names = ", ".join(f'"{c.name}"' for c in cols)
                    cur.execute(f"TRUNCATE {schema}.{table.name} RESTART IDENTITY")
                    n = 0
                    with cur.copy(f"COPY {schema}.{table.name} ({names}) FROM STDIN") as copy:
                        for row in adapter.rows(table.name):
                            copy.write_row(
                                [office_id if c.name == "office_id" else _convert(c, row.get(c.name)) for c in cols]
                            )
                            n += 1
                    counts[table.name] = n
            office = adapter.office()
            now = datetime.now(UTC)
            values = {
                "office_id": office_id,
                "name": office.get("name", office_id),
                "schema_name": schema,
                "source": adapter.source,
                "config": office,
                "last_synced_at": now,
            }
            stmt = pg_insert(offices).values(**values)
            conn.execute(stmt.on_conflict_do_update(index_elements=["office_id"], set_=values))
            conn.execute(
                update(sync_runs).where(sync_runs.c.run_id == run_id).values(status="ok", finished_at=now, rows=counts)
            )
    except Exception as exc:
        with engine.begin() as conn:
            conn.execute(
                update(sync_runs)
                .where(sync_runs.c.run_id == run_id)
                .values(status="failed", finished_at=datetime.now(UTC), error=str(exc)[:2000])
            )
        raise
    return counts
