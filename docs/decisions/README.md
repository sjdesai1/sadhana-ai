# Architecture decision records

One file per significant decision: context, decision, consequences. Never edited after acceptance;
a changed decision gets a new record that supersedes the old one.

| # | Decision | Status |
|---|---|---|
| [0001](0001-one-app-many-modules.md) | One FastAPI app with layers as modules, not services | Accepted |
| [0002](0002-schema-per-office.md) | One Postgres schema per office with row-level security | Accepted |
| [0003](0003-dagster-over-airflow.md) | Dagster for the nightly pipeline, Postgres queue for same-day events | Accepted |
| [0004](0004-office-is-a-config.md) | Every office is a config file; state rules live in their own file | Accepted |
| [0005](0005-opportunity-cost-routing.md) | Dentist time carries an hourly value when choosing who does a procedure | Accepted |
