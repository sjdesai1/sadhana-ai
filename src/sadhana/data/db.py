"""Database setup and office-scoped sessions.

- `setup_office` creates the office's schema and tables, turns on row-level security and grants the app role
- `office_session` opens a transaction that can only see one office: it switches to the app role (which
  cannot bypass row-level security), sets the office id the policies check, and points search_path at the
  office's schema

The sync job runs as the database owner because it writes every row; everything that serves users reads
through `office_session`.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import Connection, Engine, create_engine, text

from sadhana.config import settings
from sadhana.data.schema import OFFICE_TABLES, meta, metadata

APP_ROLE = "sadhana_app"
_SAFE = re.compile(r"^[a-z0-9_]+$")


def schema_for(office_id: str) -> str:
    if not _SAFE.match(office_id):
        raise ValueError(f"office id must be lowercase letters, digits and underscores: {office_id!r}")
    return f"office_{office_id}"


def get_engine(url: str | None = None) -> Engine:
    return create_engine(url or settings.database_url, pool_pre_ping=True)


def setup_shared(conn: Connection) -> None:
    """Bookkeeping schema and the app role. Safe to run every time."""
    conn.execute(text("CREATE SCHEMA IF NOT EXISTS sadhana_meta"))
    meta.create_all(conn)
    exists = conn.execute(text("SELECT 1 FROM pg_roles WHERE rolname = :r"), {"r": APP_ROLE}).scalar()
    if not exists:
        conn.execute(text(f"CREATE ROLE {APP_ROLE} NOLOGIN NOSUPERUSER NOBYPASSRLS"))
    is_super = conn.execute(text("SELECT rolsuper FROM pg_roles WHERE rolname = current_user")).scalar()
    if not is_super:
        conn.execute(text(f"GRANT {APP_ROLE} TO current_user"))
    conn.execute(text(f"GRANT USAGE ON SCHEMA sadhana_meta TO {APP_ROLE}"))
    conn.execute(text(f"GRANT SELECT ON sadhana_meta.offices TO {APP_ROLE}"))


def setup_office(conn: Connection, office_id: str) -> str:
    """Schema, tables, row-level security and grants for one office. Safe to run every time."""
    schema = schema_for(office_id)
    setup_shared(conn)
    conn.execute(text(f"CREATE SCHEMA IF NOT EXISTS {schema}"))
    metadata.create_all(conn.execution_options(schema_translate_map={None: schema}))
    for table in OFFICE_TABLES:
        name = f"{schema}.{table.name}"
        conn.execute(text(f"ALTER TABLE {name} ENABLE ROW LEVEL SECURITY"))
        conn.execute(text(f"ALTER TABLE {name} FORCE ROW LEVEL SECURITY"))
        conn.execute(text(f"DROP POLICY IF EXISTS office_isolation ON {name}"))
        conn.execute(
            text(
                f"CREATE POLICY office_isolation ON {name} "
                "USING (office_id = current_setting('sadhana.office_id', true)) "
                "WITH CHECK (office_id = current_setting('sadhana.office_id', true))"
            )
        )
    conn.execute(text(f"GRANT USAGE ON SCHEMA {schema} TO {APP_ROLE}"))
    conn.execute(text(f"GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA {schema} TO {APP_ROLE}"))
    conn.execute(text(f"GRANT USAGE ON ALL SEQUENCES IN SCHEMA {schema} TO {APP_ROLE}"))
    return schema


@contextmanager
def office_session(engine: Engine, office_id: str) -> Iterator[Connection]:
    """A transaction that sees only this office's rows, whatever table it names."""
    schema = schema_for(office_id)
    with engine.begin() as conn:
        conn.execute(text(f"SET LOCAL ROLE {APP_ROLE}"))
        conn.execute(text("SELECT set_config('sadhana.office_id', :o, true)"), {"o": office_id})
        conn.execute(text(f"SET LOCAL search_path TO {schema}, public"))
        yield conn
