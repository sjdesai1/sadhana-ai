"""Benefits engine: frequency rules, estimates, year-end phasing, payer scorecard."""

import json
from datetime import date

import pytest
from fastapi.testclient import TestClient

from sadhana.api.main import app
from sadhana.benefits.engine import BenefitsEngine, Coverage
from sadhana.benefits.rules import next_eligible, parse_freq
from sadhana.config import settings
from sadhana.simulator.population import SIM_TODAY
from sadhana.simulator.schedule import add_months

CONFIGS = settings.configs_dir
OFFICE = json.loads((CONFIGS / "offices" / "solo_manhattan.json").read_text())
client = TestClient(app)


@pytest.fixture
def benefits() -> BenefitsEngine:
    return BenefitsEngine(OFFICE, CONFIGS, SIM_TODAY)


def _plan(remaining: float = 1500, deductible_left: float = 50) -> Coverage:
    return Coverage(
        "Delta Dental PPO", 1500, remaining, 50, deductible_left, {"preventive": 1.0, "basic": 0.8, "major": 0.5}
    )


# rules, no database


def test_frequency_rules_parse() -> None:
    assert parse_freq("2 per 12 months") == (2, 12, False)
    assert parse_freq("1 per tooth per 60 months") == (1, 60, True)
    assert parse_freq("1 per tooth lifetime") == (1, None, True)
    assert parse_freq("") is None


def test_next_eligible_waits_for_the_oldest_claim_to_age_out() -> None:
    today = date(2026, 11, 1)
    two_a_year = (2, 12, False)
    assert next_eligible(two_a_year, [date(2026, 3, 10)], today) == today
    assert next_eligible(two_a_year, [date(2026, 3, 10), date(2026, 9, 2)], today) == date(2027, 3, 10)
    assert next_eligible((1, None, True), [date(2020, 1, 5)], today) is None
    assert next_eligible(None, [], today) == today


def test_estimate_applies_deductible_coverage_and_max(benefits) -> None:
    filling = {"plan_id": "T1", "code": "D2392", "tooth": "30", "priority": "needed"}
    plan = _plan()
    est = benefits.estimate("Delta Dental PPO", [filling], plan)[0]
    assert est.insurance == round((est.allowed - 50) * 0.8, 2)
    assert est.insurance + est.patient == est.allowed
    assert plan.deductible_left == 0 and plan.remaining == 1500 - est.insurance
    capped = benefits.estimate("Delta Dental PPO", [filling], _plan(remaining=40, deductible_left=0))[0]
    assert capped.insurance == 40 and capped.note == "annual max reached"


def test_estimate_handles_cash_medicaid_and_elective(benefits) -> None:
    whitening = {"code": "D9972", "priority": "elective"}
    filling = {"code": "D2391", "priority": "needed"}
    cash = benefits.estimate("Cash", [filling], None)[0]
    assert cash.insurance == 0 and cash.patient == round(cash.fee * (1 - OFFICE["cash_discount"]), 2)
    medicaid = benefits.estimate("NY Medicaid", [filling], None)[0]
    assert medicaid.patient == 0 and medicaid.insurance == medicaid.allowed
    elective = benefits.estimate("Delta Dental PPO", [whitening], _plan())[0]
    assert elective.insurance == 0 and elective.note == "elective, not covered"


def test_root_canal_is_estimated_before_the_crown_on_the_same_tooth(benefits) -> None:
    items = [{"code": c, "tooth": "3", "priority": "urgent"} for c in ("D2740", "D2950", "D3330")]
    assert [e.code for e in benefits.estimate("Delta Dental PPO", items, _plan())] == ["D3330", "D2950", "D2740"]


def test_phasing_moves_only_non_urgent_work_that_runs_past_the_max(benefits) -> None:
    items = [
        {"plan_id": "A", "code": "D3330", "tooth": "3", "priority": "urgent"},
        {"plan_id": "B", "code": "D2740", "tooth": "14", "priority": "needed"},
        {"plan_id": "C", "code": "D2740", "tooth": "19", "priority": "needed"},
    ]
    ph = benefits.phase("Delta Dental PPO", items, _plan(remaining=600, deductible_left=0))
    assert "A" in {e["plan_id"] for e in ph["do_now"]}
    assert ph["move_to_january"] and all(e["priority"] != "urgent" for e in ph["move_to_january"])
    assert ph["insurance_this_year"] <= 600
    assert ph["patient_saves"] > 0


# from the database (make up)


def test_year_end_list_never_promises_more_than_is_left(synced) -> None:
    rows = client.get("/offices/solo_manhattan/year-end?limit=1000").json()
    assert rows
    for r in rows:
        assert 0 < r["insurance_if_done_this_year"] <= r["benefits_left"] + 0.01
        assert r["days_left"] == (date(SIM_TODAY.year, 12, 31) - SIM_TODAY).days
    values = [r["insurance_if_done_this_year"] for r in rows]
    assert values == sorted(values, reverse=True)
    assert any(r["patient_saves_by_phasing"] > 0 for r in rows)


def test_patient_benefits_view(synced) -> None:
    pid = client.get("/offices/solo_manhattan/year-end?limit=1").json()[0]["patient_id"]
    body = client.get(f"/offices/solo_manhattan/patients/{pid}/benefits").json()
    assert body["plan"]["remaining"] > 0 and body["open_treatment"]
    assert sum(e["insurance"] for e in body["open_treatment"]) <= body["plan"]["remaining"] + 0.01
    assert "exam" in body["next_eligible"]
    assert client.get("/offices/solo_manhattan/patients/NOPE/benefits").status_code == 404


def test_payer_scorecard_matches_the_ledger(synced, solo_sim) -> None:
    lo, hi = add_months(SIM_TODAY, -12).isoformat(), SIM_TODAY.isoformat()
    expected = sum(x.fee for x in solo_sim.money.ledger if lo <= x.date < hi and x.code not in ("FIN", "BAD", "CXL"))
    rows = client.get("/offices/solo_manhattan/payers").json()
    assert abs(sum(r["gross"] for r in rows) - expected) < 1
    cash = next(r for r in rows if r["payer"] == "Cash")
    assert cash["claims"] == 0 and abs(cash["write_off_share"] - OFFICE["cash_discount"]) < 0.005
    ppo = [r for r in rows if r["claims"]]
    assert all(0 < r["allowed_share_of_fee"] < 1 and 0 <= r["denial_rate"] < 0.2 for r in ppo)
