"""Allowed amounts learned from paid claims, and payers paying under their schedule.

Needs Postgres (make up); skips locally without it, required in CI.
"""

import pytest
from sqlalchemy import text

from sadhana.config import settings
from sadhana.data.db import office_session
from sadhana.economics import observed
from sadhana.economics.engine import build, inputs_for
from sadhana.evals.scorecard import EvalAdapter
from sadhana.models.run import run_office
from sadhana.optimizer.run import plan_office
from sadhana.simulator.build import load_office, simulate, write
from sadhana.simulator.population import SIM_TODAY
from sadhana.sync.run import sync_office

CUT = "solo_payer_cut_test"
CFG = observed.load_settings(settings.configs_dir)


@pytest.fixture(scope="module")
def cut_office(engine, tmp_path_factory):
    sim = simulate("solo_manhattan", settings.configs_dir, seed=42, scenarios=["payer_cut"])
    sync_office(engine, EvalAdapter(CUT, "solo_manhattan", write(sim, tmp_path_factory.mktemp("cut")),
                                    settings.configs_dir, "payer cut"))  # fmt: skip
    run_office(engine, CUT, SIM_TODAY)
    return plan_office(engine, CUT, SIM_TODAY)


def test_payers_paying_their_schedule_change_nothing(engine, synced) -> None:
    office = load_office("solo_manhattan", settings.configs_dir)
    with office_session(engine, "solo_manhattan") as conn:
        rates = observed.learn(conn, inputs_for(office, settings.configs_dir), SIM_TODAY, CFG)
    assert rates.checks and not rates.flagged() and not rates.allowed
    assert all(c["paid_vs_schedule"] == pytest.approx(1, abs=0.005) for c in rates.checks)


def test_a_payer_cut_is_found_and_priced_in(engine, cut_office) -> None:
    assert cut_office.payers_flagged == ["Cigna DPPO"]
    with office_session(engine, CUT) as conn:
        check = conn.execute(text("SELECT * FROM payer_schedule_check WHERE payer = 'Cigna DPPO'")).mappings().one()
        rows = conn.execute(text("SELECT * FROM payer_rates WHERE payer = 'Cigna DPPO'")).mappings().all()
        others = conn.execute(text("SELECT count(*) FROM payer_rates WHERE payer <> 'Cigna DPPO'")).scalar()
    assert float(check["paid_vs_schedule"]) == pytest.approx(0.9, abs=0.01)  # the scenario cuts Cigna's allowed by 10%
    assert str(check["changed_on"]) in ("2025-07-01", "2025-08-01") and check["change"] < -0.03
    assert others == 0  # nobody else moved
    busy = [r for r in rows if r["claims"] >= 20]
    assert busy and all(float(r["allowed_used"]) == pytest.approx(0.9 * float(r["schedule"]), rel=0.03) for r in busy)
    # codes with no recent Cigna claims still move, by how Cigna pays across its other codes
    rare = [r for r in rows if r["claims"] == 0]
    assert rare and all(float(r["allowed_used"]) < float(r["schedule"]) for r in rare)


def test_margins_use_what_payers_really_allow(engine, cut_office) -> None:
    office = load_office("solo_manhattan", settings.configs_dir)
    with office_session(engine, CUT) as conn:
        learned = observed.load(conn)
    on_file = {
        (r["code"], r["payer"], r["provider_id"]): r for r in build(inputs_for(office, settings.configs_dir)).detail
    }
    real = {
        (r["code"], r["payer"], r["provider_id"]): r
        for r in build(inputs_for(office, settings.configs_dir, learned)).detail
    }
    cigna = [k for k in real if k[1] == "Cigna DPPO" and real[k]["basis"] == "learned from paid claims"]
    assert cigna and all(real[k]["net_margin"] < on_file[k]["net_margin"] for k in cigna)
    assert all(real[k]["net_margin"] == on_file[k]["net_margin"] for k in real if k[1] == "Delta Dental PPO")


def test_payer_check_endpoint(engine, synced) -> None:
    from fastapi.testclient import TestClient

    from sadhana.api.main import app

    plan_office(engine, "solo_manhattan", SIM_TODAY)
    body = TestClient(app).get("/offices/solo_manhattan/payer-check").json()
    assert body["payers"] and body["flagged"] == [] and body["codes_learned"] == 0
