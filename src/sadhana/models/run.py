"""Train and score every model for one office, pick each patient's next-best item, save it all."""

from __future__ import annotations

import csv
from datetime import date

from sqlalchemy import Engine, insert, text

from sadhana.benefits.engine import BenefitsEngine
from sadhana.config import settings
from sadhana.data.db import office_session, setup_office
from sadhana.data.schema import model_runs, next_best, scores
from sadhana.models import features
from sadhana.models import next_best as nb
from sadhana.models.train import ModelResult, train_missed_visit, train_recall_self_book, train_treatment_accept

MODELS = (train_missed_visit, train_recall_self_book, train_treatment_accept)


def office_context(office: dict, today: date, payers: set[str]) -> dict:
    """Fees, benefit classes and what a patient on each payer would pay for each code."""
    with (settings.configs_dir / "catalog" / "service_catalog.csv").open(newline="") as f:
        catalog = {r["code"]: r for r in csv.DictReader(f)}
    benefits = BenefitsEngine(office, settings.configs_dir, today)
    cost = {}
    for payer in payers:
        if payer not in benefits.payers:
            continue
        for code in catalog:
            est = benefits.estimate(payer, [{"code": code, "priority": "needed"}], benefits.plan(payer, None))
            cost[(payer, code)] = est[0].patient
    return {
        "fees": {c: benefits.fee(c) for c in catalog},
        "classes": {c: r["benefit_class"] for c, r in catalog.items()},
        "patient_cost": cost,
        "benefits": benefits,
    }


def run_office(engine: Engine, office_id: str, today: date) -> list[ModelResult]:
    with engine.begin() as conn:
        setup_office(conn, office_id)  # makes sure newer tables exist for offices synced before they did
        office = conn.execute(
            text("SELECT config FROM sadhana_meta.offices WHERE office_id = :o"), {"o": office_id}
        ).scalar_one()
    with office_session(engine, office_id) as conn:
        data = features.load(conn)
        consent = dict(conn.execute(text("SELECT patient_id, marketing_consent FROM patients")).all())
    ctx = office_context(office, today, set(data["patients"]["payer"].dropna()))
    results = [train(data, today, ctx) for train in MODELS]
    with office_session(engine, office_id) as conn:
        year_end = {r["patient_id"]: r["insurance_if_done_this_year"] for r in ctx["benefits"].year_end(conn)}
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
        accept = next(r for r in results if r.model == "treatment_accept").scores
        plan = data["plan"][data["plan"]["status"].isin(features.OPEN_STATUSES)]
        picks = nb.pick(plan, accept, ctx["fees"], consent, year_end)
        conn.execute(text("DELETE FROM next_best"))
        if len(picks):
            conn.execute(
                insert(next_best),
                [
                    {"office_id": office_id, **{k: (None if v != v else v) for k, v in row.items()}}
                    for row in picks.to_dict("records")
                ],
            )
    with engine.begin() as conn:
        for r in results:
            conn.execute(
                insert(model_runs).values(
                    office_id=office_id, model=r.model, model_version=r.version, as_of=today, metrics=r.metrics()
                )
            )
    return results
