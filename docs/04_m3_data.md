# Milestone 3: Data layer and sync

Goal: the product reads one database, whatever the source. The simulator is the first source; Open Dental and
other PMSs plug into the same shape later.

| Part | What it adds | Status |
| --- | --- | --- |
| 1 Canonical tables and sync | Postgres schema per office, row-level security, adapter interface, simulator adapter, full-refresh sync | Done |
| 2 Nightly job and API | Dagster sync asset ahead of the economics tables; first API endpoints on the database | Next |

## Part 1: Canonical tables and sync

Run (after `make up` and `make sim`): `make sync`, or `make sync o=solo_manhattan`. Look around with `make db`.

Code: `src/sadhana/data/schema.py` (tables), `src/sadhana/data/db.py` (setup and office sessions),
`src/sadhana/sync/adapters.py` (adapter interface and simulator adapter), `src/sadhana/sync/run.py` (sync).

| Schema | Holds |
| --- | --- |
| office_<id> | staff, patients, patient_status, appointments, procedures, teeth, conditions (diagnosed only), perio (charted only), treatment_plan, ledger, claims, payments, benefits, balances |
| sadhana_meta | offices (name, source, config, last sync) and sync_runs (start, finish, status, rows per table, error) |

How it works:
- Every office table carries `office_id` and a row-level security policy. `office_session(engine, office_id)`
  switches to the `sadhana_app` role, which cannot bypass those policies, so a query naming another office's
  table gets zero rows and an insert into it is refused
- Only observed data is stored. Hidden truth (true gum stage, undiagnosed problems, behavior traits, why a visit
  was missed) stays in the simulator files for evals
- An adapter hands back rows named like the canonical tables; the sync job never knows the source
- Sync is a full refresh: every table is truncated and reloaded with Postgres COPY in one transaction, so a
  failed sync leaves the previous data in place and is logged as failed (decision 0009)
- Both demo offices load in under 10 seconds: about 170k rows for the solo office, 530k for the group

Tests (`tests/test_sync.py`) need Postgres: locally they skip unless `make up` is running; CI runs a Postgres
service and requires them. They check row counts against the source, money totals to the cent, no truth columns
in the database, isolation between offices for reads and writes, repeatable syncs, and that a failed sync keeps
the previous data.
