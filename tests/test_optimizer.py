"""Optimizer: the slot plan fits the real schedule, respects the rules, and puts the most value in each slot.

Needs Postgres (make up); skips locally without it, required in CI.
"""

from datetime import date, timedelta

import pandas as pd
import pytest
from sqlalchemy import text

from sadhana.config import settings
from sadhana.data.db import office_session
from sadhana.models.run import run_office
from sadhana.office_calendar import Calendar, to_min
from sadhana.optimizer.plan import COSMETIC, Planner
from sadhana.optimizer.run import plan_office
from sadhana.simulator.build import load_office
from sadhana.simulator.population import SIM_TODAY


@pytest.fixture(scope="module")
def plans(engine, synced):
    out = {}
    for office_id in ("solo_manhattan", "group_queens"):
        run_office(engine, office_id, SIM_TODAY)
        out[office_id] = plan_office(engine, office_id, SIM_TODAY)
    return out


def _intervals(conn) -> pd.DataFrame:  # noqa: ANN001
    rows = conn.execute(
        text(
            "SELECT provider_id, date, to_char(start, 'HH24:MI'), minutes, patient_id, 'plan' FROM slot_plan "
            "UNION ALL SELECT provider_id, date, to_char(start, 'HH24:MI'), "
            "(extract(epoch FROM \"end\" - start) / 60)::int, patient_id, 'booked' FROM appointments "
            "WHERE status = 'scheduled'"
        )
    ).all()
    df = pd.DataFrame(rows, columns=["provider_id", "date", "start", "minutes", "patient_id", "source"])
    df["s"] = df["start"].map(to_min)
    df["e"] = df["s"] + df["minutes"]
    return df


def test_gaps_skip_booked_time_and_lunch() -> None:
    office = load_office("solo_manhattan", settings.configs_dir)
    cal = Calendar(office, set())
    d = date(2026, 11, 4)  # a Wednesday
    pid = cal.providers(d, "doctor")[0]
    open_, close = cal.hours(d)
    full = cal.gaps(d, pid, 20)
    cal.book(d, pid, open_, 60)
    after = cal.gaps(d, pid, 20)
    assert after[0][0] == open_ + 60 + cal.turnover
    lunch = cal.lunch
    for start, usable in after:
        assert start + usable <= close
        assert start + usable <= lunch[0] or start >= lunch[1]
    assert sum(u for _, u in after) < sum(u for _, u in full)


def test_plan_never_double_books_a_provider_or_a_patient(engine, plans) -> None:
    for office_id, result in plans.items():
        assert result.fills, office_id
        with office_session(engine, office_id) as conn:
            df = _intervals(conn)
        for _, g in df.groupby(["provider_id", "date"]):
            g = g.sort_values("s")
            assert (g["s"].iloc[1:].to_numpy() >= g["e"].iloc[:-1].to_numpy()).all()
        planned = df[df["source"] == "plan"]
        assert planned["patient_id"].is_unique
        booked = set(df.loc[df["source"] == "booked", "patient_id"])
        assert not set(planned["patient_id"]) & booked  # people already on the books are not offered more


def test_plan_stays_inside_hours_horizon_and_lead_time(plans) -> None:
    office = load_office("group_queens", settings.configs_dir)
    cal = Calendar(office, set())
    for f in plans["group_queens"].fills:
        d = date.fromisoformat(f.date)
        assert SIM_TODAY + timedelta(days=2) <= d <= SIM_TODAY + timedelta(days=14)
        open_, close = cal.hours(d)
        s = to_min(f.start)
        assert open_ <= s and s + f.minutes <= close
        assert f.provider_id in cal.providers(d, f.column)


def test_plan_follows_the_clinical_and_consent_rules(engine, plans) -> None:
    for office_id, result in plans.items():
        office = load_office(office_id, settings.configs_dir)
        planner = Planner(office, settings.configs_dir, SIM_TODAY)
        with office_session(engine, office_id) as conn:
            consent = dict(conn.execute(text("SELECT patient_id, marketing_consent FROM patients")).all())
            plan = {
                r[0]: r[1:] for r in conn.execute(text("SELECT plan_id, patient_id, status FROM treatment_plan")).all()
            }
        for f in result.fills:
            assert f.provider_id in planner.eligible[f.code]  # scope of practice, certs, specialist scope
            assert f.expected_value > 0 and f.margin > 0
            if f.plan_id:  # diagnosed work only, the patient's own, still open
                owner, status = plan[f.plan_id]
                assert owner == f.patient_id and status in ("proposed", "accepted")
            else:
                assert f.code in ("D1110", "D1120", "D4910")
            if f.code in COSMETIC:
                assert consent[f.patient_id]


def test_plan_beats_a_first_come_first_served_desk(engine, plans) -> None:
    """The plan's expected value against the desk calling the same candidates in list order."""
    result = plans["group_queens"]
    office = load_office("group_queens", settings.configs_dir)
    planner = Planner(office, settings.configs_dir, SIM_TODAY)
    # rebuild the same open time and candidates, then fill each stretch with the first candidate that fits
    from sadhana.optimizer import run as R

    with office_session(engine, "group_queens") as conn:
        booked = pd.DataFrame(
            conn.execute(
                text(
                    "SELECT appt_id, patient_id, provider_id, \"column\", date, to_char(start, 'HH24:MI'), "
                    "to_char(\"end\", 'HH24:MI'), codes, NULL FROM appointments WHERE status = 'scheduled' "
                    "AND date > :t AND date <= :e"
                ),
                {"t": SIM_TODAY, "e": SIM_TODAY + timedelta(days=14)},
            ).all(),
            columns=["appt_id", "patient_id", "provider_id", "column", "date", "start", "end", "codes", "payer"],
        )
        future = {r[0] for r in conn.execute(text("SELECT DISTINCT patient_id FROM appointments WHERE status = "
                                                  "'scheduled' AND date > :t"), {"t": SIM_TODAY})}  # fmt: skip
        from sadhana.models import features

        data = features.load(conn)
        cands = planner.candidates(
            data["patients"], R._scores(conn, "recall_self_book"), data["plan"],
            R._scores(conn, "treatment_accept"), {}, future,
        )  # fmt: skip
    blocks = planner.blocks(booked)
    used, naive = set(), 0.0
    for b in sorted(blocks, key=lambda b: (b["date"], b["start"])):
        for c in sorted(cands, key=lambda c: c["patient_id"]):
            if c["patient_id"] in used:
                continue
            _, ev = planner._value(b, c)
            if ev > 0:
                used.add(c["patient_id"])
                naive += ev
                break
    assert result.expected_value > 1.5 * naive


def test_at_risk_visits_are_booked_and_have_backups(engine, plans) -> None:
    result = plans["group_queens"]
    assert result.at_risk
    with office_session(engine, "group_queens") as conn:
        scheduled = {r[0] for r in conn.execute(text("SELECT appt_id FROM appointments WHERE status = 'scheduled'"))}
    planned = {f.patient_id for f in result.fills}
    for a in result.at_risk:
        assert a.appt_id in scheduled and a.p_missed >= 0.15
        if a.backup_patient_id:
            assert a.backup_patient_id not in planned and a.backup_patient_id != a.patient_id
    assert sum(1 for a in result.at_risk if a.backup_patient_id) >= len(result.at_risk) // 2


def test_opportunities_endpoint(plans) -> None:
    from fastapi.testclient import TestClient

    from sadhana.api.main import app

    body = TestClient(app).get("/offices/group_queens/opportunities").json()
    assert body["offers"] == len(plans["group_queens"].fills)
    assert body["expected_value"] == pytest.approx(plans["group_queens"].expected_value, abs=1)
    assert all(s["reason"] and s["start"] for s in body["slots"]) and body["at_risk"]
    week = TestClient(app).get("/offices/group_queens/opportunities?days=7").json()
    assert 0 < week["offers"] < body["offers"]
