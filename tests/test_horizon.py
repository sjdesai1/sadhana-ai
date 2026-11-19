"""The 90-day plan and overbooking.

Needs Postgres (make up); skips locally without it, required in CI.
"""

from datetime import date

import numpy as np
import pytest
from sqlalchemy import text

from sadhana.config import settings
from sadhana.data.db import office_session
from sadhana.models.run import run_office
from sadhana.optimizer.horizon import backtest, forecast_office
from sadhana.optimizer.plan import Planner
from sadhana.optimizer.run import gather, plan_office
from sadhana.simulator.build import load_office
from sadhana.simulator.population import SIM_TODAY

OFFICES = ("solo_manhattan", "group_queens")


@pytest.fixture(scope="module")
def plans(engine, synced):
    out = {}
    for office_id in OFFICES:
        run_office(engine, office_id, SIM_TODAY)
        plan_office(engine, office_id, SIM_TODAY)
        out[office_id] = forecast_office(engine, office_id, SIM_TODAY)
    return out


@pytest.mark.parametrize("office_id", OFFICES)
@pytest.mark.parametrize("as_of", [date(2026, 2, 1), date(2026, 5, 3), date(2026, 8, 2)])
def test_baseline_forecast_matches_past_quarters(engine, synced, office_id, as_of) -> None:
    """Made on a past date from the schedule as it stood then, next to what really happened."""
    b = backtest(engine, office_id, as_of)
    b = b[b["actual_hours"] > 0]
    assert b["week"].nunique() >= 12
    hyg = b[b["column"] == "hygiene"]
    weekly = float(np.mean(abs(hyg["expected_hours"] - hyg["actual_hours"]) / hyg["actual_hours"]))
    assert weekly < 0.10
    assert abs(hyg["expected_hours"].sum() / hyg["actual_hours"].sum() - 1) < 0.06
    doc = b[b["column"] == "doctor"]
    assert abs(doc["expected_hours"].sum() / doc["actual_hours"].sum() - 1) < 0.25


def test_plan_adds_up(engine, plans) -> None:
    for office_id, plan in plans.items():
        w = plan.weeks
        assert w["week"].nunique() == 13 and w["week"].min() > SIM_TODAY
        assert (w["expected_hours"] <= w["capacity_hours"] + 1e-6).all()
        added = w["recall_hours"] + w["treatment_hours"]
        assert (added <= w["open_hours"] + 1e-6).all() and (w["unfilled_hours"] >= 0).all()
        assert np.allclose(w["unfilled_hours"], (w["open_hours"] - added).clip(lower=0))
        s = plan.summary()
        assert 0 < s["utilization"] < s["utilization_with_outreach"] <= 1
        assert s["outreach_value"] > 0 and s["benefits_expiring"] > 0
        with office_session(engine, office_id) as conn:
            assert conn.execute(text("SELECT count(*) FROM plan_90")).scalar() == len(w)


def test_agent_bookings_count_as_booked(engine, plans) -> None:
    office_id = "solo_manhattan"
    with office_session(engine, office_id) as conn:  # start clean: earlier runs may have left agent bookings
        conn.execute(text("DELETE FROM pending_bookings"))
    before = forecast_office(engine, office_id, SIM_TODAY).weeks.set_index(["week", "column"])["booked_hours"]
    wk = before.index[0][0]
    with office_session(engine, office_id) as conn:
        conn.execute(
            text(
                "INSERT INTO pending_bookings (office_id, booking_id, kind, patient_id, date, start, minutes, "
                "provider_id, \"column\", source, created_on, status) VALUES (:o, 'b-test', 'book', 'P000001', "
                ":d, '09:00', 60, 'H1', 'hygiene', 'reply', :t, 'pending')"
            ),
            {"o": office_id, "d": wk, "t": SIM_TODAY},
        )
    after = forecast_office(engine, office_id, SIM_TODAY).weeks.set_index(["week", "column"])["booked_hours"]
    assert after[(wk, "hygiene")] == pytest.approx(before[(wk, "hygiene")] + 1.0)
    with office_session(engine, office_id) as conn:
        conn.execute(text("DELETE FROM pending_bookings"))


def test_overbooking_only_where_it_pays(engine, plans) -> None:
    office = load_office("group_queens", settings.configs_dir)
    planner = Planner(office, settings.configs_dir, SIM_TODAY)
    with office_session(engine, "group_queens") as conn:
        g = gather(conn, SIM_TODAY, date(2026, 11, 15))
    booked = g["booked"]
    cands = planner.candidates(g["patients"], g["recall"], g["plan"], g["accept"], g["miss"], g["future"])
    likely = {a: 0.6 for a in booked["appt_id"]}
    unlikely = {a: 0.05 for a in booked["appt_id"]}
    picks = planner.overbook(booked, likely, cands, set())
    assert picks and not planner.overbook(booked, unlikely, cands, set())
    per_day: dict[tuple[str, str], int] = {}
    for o in picks:
        assert o.column != "specialist" and o.net > 0 and o.expected_gain > o.expected_cost
        per_day[(o.provider_id, o.date)] = per_day.get((o.provider_id, o.date), 0) + 1
    assert max(per_day.values()) <= planner.cfg["overbooking"]["max_per_provider_day"]
    assert len({o.extra_patient_id for o in picks}) == len(picks)


def test_plan_90_endpoint(plans) -> None:
    from fastapi.testclient import TestClient

    from sadhana.api.main import app

    client = TestClient(app)
    body = client.get("/offices/group_queens/plan-90").json()
    assert len(body["weeks"]) == len(plans["group_queens"].weeks)
    assert body["totals"]["outreach_value"] == pytest.approx(plans["group_queens"].summary()["outreach_value"], abs=5)
    assert "overbook" in client.get("/offices/group_queens/opportunities").json()
