"""Life events, market scenarios, stress settings and hidden test offices."""

from collections import Counter
from datetime import date

from sadhana.config import settings
from sadhana.simulator import history, money
from sadhana.simulator.build import simulate
from sadhana.simulator.population import SIM_TODAY

CONFIGS = settings.configs_dir


def _no_show_rate(sim) -> float:
    past = [a for a in sim.appointments if a.date < SIM_TODAY.isoformat()]
    return sum(a.status == "no_show" for a in past) / len(past)


def _completed_value(sim) -> float:
    return money.summary(sim.money)["gross"]


def test_hidden_offices_still_look_like_real_offices(holdout_sims) -> None:
    for name, sim in holdout_sims.items():
        m = money.summary(sim.money)
        assert 0.03 <= _no_show_rate(sim) <= 0.11, name
        assert 0.65 <= history.utilization(sim.appointments, sim.office, "hygiene") <= 0.95, name
        assert 0.30 <= history.utilization(sim.appointments, sim.office, "doctor") <= 0.90, name
        assert 0.10 <= history.overdue_share(sim.history, sim.flows, sim.perio) <= 0.50, name
        assert 0.90 <= m["collection_rate"] <= 1.02, name


def test_payer_mix_drives_write_offs_across_offices(holdout_sims) -> None:
    wo = {n: money.summary(s.money)["write_off_share"] for n, s in holdout_sims.items()}
    assert wo["holdout_bronx_medicaid"] > wo["holdout_park_slope_family"] > wo["holdout_ues_boutique"]


def test_job_loss_moves_billing_from_ppo_to_cash(solo_sim) -> None:
    lost = {e.household_id: e for e in solo_sim.life_events if e.event == "job_loss"}
    assert lost
    house = {p.patient_id: p.household_id for p in solo_sim.patients}
    checked = 0
    for x in solo_sim.money.ledger:
        e = lost.get(house[x.patient_id])
        if e is None or x.code in ("FIN", "BAD"):
            continue
        assert x.payer == (e.from_payer if x.date < e.date else e.to_payer)
        checked += 1
    assert checked > 0


def test_first_claim_after_an_insurance_change_often_hits_the_old_card(solo_sim, group_sim) -> None:
    for sim in (solo_sim, group_sim):
        stale = [c for c in sim.money.claims if c.denial_reason == "coverage terminated"]
        assert stale and all(c.status in ("paid_after_resubmit", "pending") for c in stale)


def test_stress_settings_change_behavior(solo_sim) -> None:
    worse = simulate("solo_manhattan", CONFIGS, seed=42, overrides={"outcomes": {"no_show_multiplier": 2.0}})
    ratio = _no_show_rate(worse) / _no_show_rate(solo_sim)
    assert 1.5 <= ratio <= 2.5
    stingy = simulate("solo_manhattan", CONFIGS, seed=42, overrides={"treatment": {"accept_shift": -1.5}})
    assert _completed_value(stingy) < 0.9 * _completed_value(solo_sim)


def test_recession_cuts_elective_and_needed_yeses(solo_sim) -> None:
    hard = simulate("solo_manhattan", CONFIGS, seed=42, scenarios=["recession"])

    def yes_rate(sim) -> float:
        rows = [
            x
            for x in sim.plan
            if x.priority in ("needed", "elective")
            and "2025-03-01" <= x.status_on < "2026-03-01"
            and x.status in ("accepted", "scheduled", "completed", "declined")
        ]
        return sum(x.status != "declined" for x in rows) / len(rows)

    assert yes_rate(hard) < yes_rate(solo_sim)


def test_payer_fee_cut_lowers_allowed_amounts(holdout_sims) -> None:
    sim = holdout_sims["holdout_park_slope_family"]
    allowed = {"before": set(), "after": set()}
    for x in sim.money.ledger:
        if x.payer == "Cigna DPPO" and x.code == "D1110":
            allowed["after" if x.date >= "2025-07-01" else "before"].add(x.allowed)
    assert allowed["before"] and allowed["after"]
    assert abs(max(allowed["after"]) / max(allowed["before"]) - 0.9) < 0.01


def test_competitor_pulls_patients_away(solo_sim) -> None:
    rival = simulate("solo_manhattan", CONFIGS, seed=42, scenarios=["competitor"])

    def left_after(sim) -> int:
        return sum(1 for f in sim.flows if f.left_on and f.left_on >= "2025-09-01")

    assert left_after(rival) > left_after(solo_sim)


def test_life_events_are_dated_inside_the_window(solo_sim) -> None:
    kinds = Counter(e.event for e in solo_sim.life_events)
    assert {"job_loss", "plan_switch", "new_job"} <= set(kinds)
    for e in solo_sim.life_events:
        assert date(2023, 11, 1) <= date.fromisoformat(e.date) < SIM_TODAY
