"""Train and score every model for one office, then save the scores and the test metrics."""

from __future__ import annotations

from datetime import date

from sqlalchemy import Engine, insert, text

from sadhana.data.db import office_session, setup_office
from sadhana.data.schema import model_runs, scores
from sadhana.models import features
from sadhana.models.train import ModelResult, train_missed_visit, train_recall_self_book

MODELS = (train_missed_visit, train_recall_self_book)


def run_office(engine: Engine, office_id: str, today: date) -> list[ModelResult]:
    with engine.begin() as conn:
        setup_office(conn, office_id)  # makes sure the scores table exists for offices synced before it did
    with office_session(engine, office_id) as conn:
        data = features.load(conn)
    results = [train(data, today) for train in MODELS]
    with office_session(engine, office_id) as conn:
        for r in results:
            conn.execute(text("DELETE FROM scores WHERE model = :m"), {"m": r.model})
            rows = [
                {
                    "office_id": office_id,
                    "model": r.model,
                    "entity_id": e,
                    "patient_id": p,
                    "score": round(float(s), 4),
                    "model_version": r.version,
                }
                for e, p, s in r.scores.itertuples(index=False)
            ]
            if rows:
                conn.execute(insert(scores), rows)
    with engine.begin() as conn:
        for r in results:
            conn.execute(
                insert(model_runs).values(
                    office_id=office_id, model=r.model, model_version=r.version, as_of=today, metrics=r.metrics()
                )
            )
    return results
