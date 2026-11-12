"""Nightly models: tested on months they never saw, graded against the simulator's hidden truth.

Needs Postgres (make up); skips locally without it, required in CI.
"""

import pandas as pd
import pytest
from scipy.stats import spearmanr
from sklearn.metrics import roc_auc_score
from sqlalchemy import text

from sadhana.benefits.rules import add_months
from sadhana.config import settings
from sadhana.data.db import office_session
from sadhana.models import features as F
from sadhana.models.run import run_office
from sadhana.simulator.build import simulate, write
from sadhana.simulator.population import SIM_TODAY
from sadhana.sync.adapters import SimulatorAdapter
from sadhana.sync.run import sync_office

FORBIDDEN = {"language", "zip", "neighborhood", "sex"}


@pytest.fixture(scope="module")
def solo_models(engine, synced):
    return {r.model: r for r in run_office(engine, "solo_manhattan", SIM_TODAY)}


def test_models_never_use_language_zip_or_sex() -> None:
    for feats in (F.MISSED_FEATURES, F.RECALL_FEATURES):
        assert not {f for f in feats if any(x in f for x in FORBIDDEN)}


def test_missed_visit_model_beats_chance_and_is_calibrated(solo_models) -> None:
    r = solo_models["missed_visit"]
    # the ceiling here is low (about 0.63-0.68, see the test below): most no-shows are not predictable
    assert r.auc > 0.58 and r.calibration_error < 0.05
    assert 0.05 < r.base_rate < 0.2


def test_recall_model_beats_chance_and_is_calibrated(solo_models) -> None:
    r = solo_models["recall_self_book"]
    assert r.auc > 0.62 and r.calibration_error < 0.08


def test_scores_track_each_patients_hidden_behavior(solo_models, solo_sim) -> None:
    truth = pd.DataFrame(
        {
            "patient_id": [t.patient_id for t in solo_sim.truths],
            "miss": [t.no_show_rate + t.late_cancel_rate for t in solo_sim.truths],
        }
    )
    missed = solo_models["missed_visit"].scores.groupby("patient_id")["score"].mean().reset_index()
    m = missed.merge(truth, on="patient_id")
    assert spearmanr(m["score"], m["miss"]).statistic > 0.2
    # overdue patients the simulator knows have quietly stopped coming should score low
    lapsed = {f.patient_id for f in solo_sim.flows if f.lapsed_on and f.lapsed_on < SIM_TODAY.isoformat()}
    recall = solo_models["recall_self_book"].scores
    assert roc_auc_score(recall["patient_id"].isin(lapsed), -recall["score"]) > 0.6


def test_scores_cover_what_is_open_today(engine, solo_models) -> None:
    with office_session(engine, "solo_manhattan") as conn:
        upcoming = conn.execute(
            text("SELECT count(*) FROM appointments WHERE status = 'scheduled' AND date >= :d"), {"d": SIM_TODAY}
        ).scalar()
        stored = dict(conn.execute(text("SELECT model, count(*) FROM scores GROUP BY model")).all())
        bounds = conn.execute(text("SELECT min(score), max(score) FROM scores")).one()
    assert stored["missed_visit"] == upcoming
    assert stored["recall_self_book"] == len(solo_models["recall_self_book"].scores) > 50
    assert 0 <= bounds[0] <= bounds[1] <= 1
    with engine.connect() as conn:
        runs = conn.execute(
            text("SELECT count(DISTINCT model) FROM sadhana_meta.model_runs WHERE office_id = 'solo_manhattan'")
        ).scalar()
    assert runs == 3


def test_history_features_only_look_backward(engine, synced) -> None:
    with office_session(engine, "solo_manhattan") as conn:
        data = F.load(conn)
    rows = F.missed_visit_frame(data).sample(200, random_state=1)
    appts = data["appointments"]
    resolved = appts[appts["status"].isin(("completed", "no_show", "late_cancel"))]
    for _, r in rows.iterrows():
        before = resolved[(resolved["patient_id"] == r["patient_id"]) & (resolved["date"] < r["booked_on"])]
        assert r["prior_visits"] == len(before)


def test_models_hold_up_on_a_hidden_office(engine, tmp_path) -> None:
    sim = simulate("holdout_bronx_medicaid", settings.configs_dir, seed=42)
    sync_office(engine, SimulatorAdapter("holdout_bronx_medicaid", write(sim, tmp_path), settings.configs_dir))
    results = {r.model: r for r in run_office(engine, "holdout_bronx_medicaid", SIM_TODAY)}
    assert results["missed_visit"].auc > 0.6
    assert results["recall_self_book"].auc > 0.6


def test_missed_visit_model_gets_close_to_the_best_possible(engine, solo_models, solo_sim) -> None:
    # the best any model could do: rank test visits by each patient's hidden miss rate

    truth = {t.patient_id: t.no_show_rate + t.late_cancel_rate for t in solo_sim.truths}
    with office_session(engine, "solo_manhattan") as conn:
        rows = F.missed_visit_frame(F.load(conn))
    test = rows[rows["missed"].notna() & (rows["date"] >= pd.Timestamp(add_months(SIM_TODAY, -4)))]
    best = roc_auc_score(test["missed"], test["patient_id"].map(truth))
    assert solo_models["missed_visit"].auc > best - 0.08


# treatment acceptance and next-best item


@pytest.fixture(scope="module")
def group_models(engine, synced):
    return {r.model: r for r in run_office(engine, "group_queens", SIM_TODAY)}


def test_treatment_model_beats_chance_and_is_calibrated(solo_models, group_models) -> None:
    group = group_models["treatment_accept"]
    assert group.auc > 0.65 and group.calibration_error < 0.07
    # a solo office has a tenth of the data; pooled learning across offices comes later
    assert solo_models["treatment_accept"].auc > 0.55


def test_treatment_scores_cover_open_work_and_rank_by_urgency(engine, group_models) -> None:
    with office_session(engine, "group_queens") as conn:
        open_items = dict(
            conn.execute(
                text("SELECT plan_id, priority FROM treatment_plan WHERE status IN ('proposed', 'accepted')")
            ).all()
        )
    scores = group_models["treatment_accept"].scores
    assert set(scores["entity_id"]) == set(open_items)
    by_priority = scores.assign(priority=scores["entity_id"].map(open_items)).groupby("priority")["score"].mean()
    assert by_priority["urgent"] > by_priority["needed"] > by_priority["elective"]


def test_next_best_is_one_diagnosed_item_per_patient(engine, group_models) -> None:
    with office_session(engine, "group_queens") as conn:
        picks = (
            conn.execute(
                text(
                    "SELECT n.patient_id, n.plan_id, n.code, n.p_book, n.value, n.expected_value, t.status, "
                    "t.patient_id AS owner, p.marketing_consent "
                    "FROM next_best n JOIN treatment_plan t USING (plan_id) "
                    "JOIN patients p ON p.patient_id = n.patient_id"
                )
            )
            .mappings()
            .all()
        )
    assert picks and len({p["patient_id"] for p in picks}) == len(picks)
    for p in picks:
        assert p["owner"] == p["patient_id"] and p["status"] in ("proposed", "accepted")
        if p["code"] in ("D9972", "D8090", "D2962"):
            assert p["marketing_consent"]
        # chance x fee x priority weight (urgent counts 1.5x); the stored chance is rounded to 4 places
        assert float(p["expected_value"]) <= float(p["p_book"]) * float(p["value"]) * 1.5 + 1


def test_next_best_endpoint(engine, group_models) -> None:
    from fastapi.testclient import TestClient

    from sadhana.api.main import app

    rows = TestClient(app).get("/offices/group_queens/next-best?limit=10").json()
    assert len(rows) == 10
    values = [r["expected_value"] for r in rows]
    assert values == sorted(values, reverse=True) and all(r["reason"] for r in rows)
