# 0009 Full-refresh sync, schema from code

Status: Accepted

## Context
- v1 runs on simulated offices; the data model will still change often
- Real PMS syncs need incremental loads, change tracking and migrations, but not before the first real office

## Decision
- Sync reloads an office completely: truncate every table and COPY the new rows in one transaction
- Tables are created from the SQLAlchemy definitions in `data/schema.py`; no migration tool yet
- Every run is logged in `sadhana_meta.sync_runs` with row counts or the error

## Consequences
- Simple and safe: readers always see a whole sync, and a failure keeps the previous data
- Changing a table means re-running the sync, which is fine while every office is simulated
- Before the first real office (v3): add Alembic migrations and incremental sync by changed-since timestamps
