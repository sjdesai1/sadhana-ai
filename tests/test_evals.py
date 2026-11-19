"""The scorecard: grading, rendering, one real run, and the product fixes the scorecard found.

Needs Postgres (make up) for the run tests; skips locally without it, required in CI.
"""

from datetime import timedelta

import pytest
from sqlalchemy import text

from sadhana.config import settings
from sadhana.data.db import office_session
from sadhana.evals.scorecard import Check, grade, load_settings, render, run_one
from sadhana.optimizer.run import plan_office
from sadhana.simulator.population import SIM_TODAY

CFG = load_settings(settings.configs_dir)


def test_grading() -> None:
    checks = {"a": [">=", 0.6], "b": ["<=", 0.1], "c": ["==", 0], "d": [">=", 1]}
    got = {c.name: c for c in grade({"a": 0.61, "b": 0.2, "c": 0, "d": float("nan")}, checks)}
    assert got["a"].status == "pass" and got["b"].status == "fail" and got["c"].status == "pass"
    assert got["d"].status == "n/a"
    assert isinstance(got["a"], Check)


def test_every_run_has_a_known_office_and_every_check_a_known_op() -> None:
    known = {p.stem for d in ("offices", "offices_holdout") for p in (settings.configs_dir / d).glob("*.json")}
    assert {r["office"] for r in CFG["runs"]} <= known
    assert len({r["label"] for r in CFG["runs"]}) == len(CFG["runs"])
    assert all(op in (">=", "<=", "==") for op, _ in CFG["checks"].values())
    assert sum(r.get("holdout", False) for r in CFG["runs"]) == 3


def test_render_marks_failures_and_crashes() -> None:
    ok = {"label": "x", "seed": 42, "passed": True, "info": {"messages": 10},
          "checks": [{"name": "m", "value": 0.7, "op": ">=", "threshold": 0.6, "status": "pass"}]}  # fmt: skip
    bad = {**ok, "label": "y", "passed": False,
           "checks": [{"name": "m", "value": 0.5, "op": ">=", "threshold": 0.6, "status": "fail"}]}  # fmt: skip
    crash = {"label": "z", "seed": 42, "passed": False, "error": "ValueError: boom", "checks": [], "info": {}}
    md = render([ok, bad, crash])
    assert "1 of 3 runs pass" in md and "FAIL" in md and "z crashed: ValueError: boom" in md


@pytest.fixture(scope="module")
def ues(engine, tmp_path_factory):
    run = next(r for r in CFG["runs"] if r["label"] == "holdout_ues_boutique")
    return run_one(engine, run, {**CFG, "pilot_days": 10}, tmp_path_factory.mktemp("evals"))


def test_a_real_run_keeps_every_rule(ues) -> None:
    """Thresholds on a hidden office are reported, not required here; the rules always are."""
    m = ues["metrics"]
    assert m["data.hidden_truth_columns"] == 0 and m["models.protected_inputs"] == 0
    assert m["optimizer.double_bookings"] == 0 and m["replies.double_bookings"] == 0
    assert m["outreach.rule_violations"] == 0 and m["replies.refill_winners_per_slot"] <= 1
    assert m["optimizer.plan_vs_first_come"] > 1
    assert {c["name"] for c in ues["checks"]} == set(CFG["checks"])


def test_runs_repeat_exactly(engine, ues, tmp_path_factory) -> None:
    """Same simulation, same seed: same scorecard (no leftovers from the run before, no unordered reads)."""
    run = next(r for r in CFG["runs"] if r["label"] == "holdout_ues_boutique")
    again = run_one(engine, run, {**CFG, "pilot_days": 10}, tmp_path_factory.mktemp("evals"))
    assert again["metrics"] == ues["metrics"] and again["info"] == ues["info"]


def test_slot_plan_avoids_the_agents_own_bookings(engine, ues) -> None:
    """Found by the scorecard: the plan used to offer times the agents had already booked by text."""
    label = "holdout_ues_boutique"
    plan_office(engine, label, SIM_TODAY)
    with office_session(engine, label) as conn:
        conn.execute(text("DELETE FROM pending_bookings"))
        first = conn.execute(
            text('SELECT date, start, minutes, provider_id, "column" FROM slot_plan ORDER BY date, start LIMIT 1')
        ).one()
        conn.execute(
            text(
                "INSERT INTO pending_bookings (office_id, booking_id, kind, patient_id, date, start, minutes, "
                "provider_id, \"column\", source, created_on, status) VALUES (:o, 'b-test', 'book', 'P-test', :d, "
                ":s, :m, :p, :c, 'reply', :t, 'pending')"
            ),
            {"o": label, "d": first.date, "s": first.start, "m": first.minutes, "p": first.provider_id,
             "c": first.column, "t": SIM_TODAY},
        )  # fmt: skip
    plan_office(engine, label, SIM_TODAY)
    with office_session(engine, label) as conn:
        clash = conn.execute(
            text(
                "SELECT count(*) FROM slot_plan s JOIN pending_bookings b ON b.provider_id = s.provider_id AND "
                "b.date = s.date AND s.start < b.start + make_interval(mins => b.minutes) AND "
                "b.start < s.start + make_interval(mins => s.minutes)"
            )
        ).scalar()
        conn.execute(text("DELETE FROM pending_bookings"))
    assert clash == 0
    assert first.date <= SIM_TODAY + timedelta(days=14)
