# Architecture

Five layers, one loop: every outcome retrains the models nightly. Full spec lives in the Sadhana AI v1 spec doc.

| Layer | Pieces | Status |
| --- | --- | --- |
| Inputs | PMS sync, simulator, office config, payer data | Office config and payer data done |
| Data | Adapters, practice schema, economics, event log | Economics done |
| Intelligence | Nightly models, benefits, recommender, optimizer | Next |
| Agents | Outreach, conversation, booking, staff queue | Later |
| Outputs | Huddle report, revenue report, eval harness, ops agents | Later |

## Where things run

- FastAPI app: API and agent endpoints, `src/sadhana/api`
- Dagster: nightly assets, `src/sadhana/pipelines/definitions.py`
- Postgres: one schema per office, row-level security
- Postgres job queue: same-day events like cancellations and replies
