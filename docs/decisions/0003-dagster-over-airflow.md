# 0003 Dagster for nightly work, Postgres queue for same-day events

Status: Accepted

## Context
- Nightly work is a chain of data assets: synced data, features, scores, benefits, the 90-day plan, reports
- Same-day events (a cancellation at 9am, a patient reply) need seconds, not a schedule
- The readmission project already uses Airflow

## Decision
- Dagster for the nightly pipeline: assets show what is stale and rerun only that piece; lighter on one server
- A Postgres-based job queue for same-day events, so no extra service

## Consequences
- Fewer containers to run and watch
- A second orchestrator in the portfolio next to Airflow
