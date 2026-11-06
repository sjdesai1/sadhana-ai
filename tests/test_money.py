"""Billing behaves like a real office: claims, benefits, patient portions and balances add up."""

from collections import defaultdict
from datetime import date

from sadhana.simulator import money
from sadhana.simulator.population import SIM_TODAY
from sadhana.simulator.schedule import add_months

ADJUSTMENTS = {"FIN", "BAD"}


def test_every_line_adds_up(solo_sim, group_sim) -> None:
    for sim in (solo_sim, group_sim):
        for x in sim.money.ledger:
            if x.code in ADJUSTMENTS:
                continue
            total = x.primary_paid + x.secondary_paid + x.patient_portion + x.write_off
            assert abs(x.fee - total) < 0.05, x
            assert min(x.primary_paid, x.secondary_paid, x.patient_portion) >= 0


def test_medicaid_patients_owe_nothing_for_covered_work_and_no_cancellation_fees(group_sim) -> None:
    for x in group_sim.money.ledger:
        if x.payer == "NY Medicaid":
            assert x.code != "CXL"
            if x.primary_paid > 0:
                assert x.patient_portion == 0


def test_annual_max_is_never_exceeded(solo_sim, group_sim) -> None:
    for sim in (solo_sim, group_sim):
        for b in sim.money.benefits:
            assert -0.01 <= b.used <= b.annual_max + 0.01
            assert b.deductible_met <= b.deductible + 0.01


def test_cleanings_respect_two_a_year(solo_sim) -> None:
    paid = defaultdict(list)
    for x in solo_sim.money.ledger:
        if x.code == "D1110" and x.primary_paid > 0:
            paid[x.patient_id].append(date.fromisoformat(x.date))
    for dates in paid.values():
        dates.sort()
        for i, d in enumerate(dates):
            assert sum(1 for e in dates[: i + 1] if e > add_months(d, -12)) <= 2


def test_waiting_period_holds_back_major_work(group_sim) -> None:
    joined = {f.patient_id: f.joined_on for f in group_sim.flows}
    waiting = {"Aetna DPPO", "Guardian"}
    held = 0
    for x in group_sim.money.ledger:
        if x.payer in waiting and x.note == "waiting period":
            assert x.primary_paid == 0
            assert date.fromisoformat(x.date) < add_months(date.fromisoformat(joined[x.patient_id]), 12)
            held += 1
    assert held > 0


def test_cash_patients_get_the_discount(solo_sim) -> None:
    discount = solo_sim.office["cash_discount"]
    for x in solo_sim.money.ledger:
        if x.payer == "Cash" and x.code not in ADJUSTMENTS | {"CXL"}:
            assert abs(x.allowed - x.fee * (1 - discount)) < 0.05


def test_payments_happen_before_today_and_after_the_visit(solo_sim) -> None:
    claims = {c.claim_id: c for c in solo_sim.money.claims}
    for p in solo_sim.money.payments:
        assert p.date < SIM_TODAY.isoformat()
        if p.source == "insurance":
            c = claims[p.ref]
            assert c.status in ("paid", "paid_after_resubmit") and p.date >= c.submitted_on


def test_headline_numbers_match_real_offices(solo_sim, group_sim) -> None:
    solo, group = money.summary(solo_sim.money), money.summary(group_sim.money)
    for m in (solo, group):
        assert 0.93 <= m["collection_rate"] <= 1.02
        assert 0.03 <= m["first_pass_denial"] <= 0.12
        assert 14 <= m["median_days_to_pay"] <= 45
    # PPO write-offs typically 25-40%; a Medicaid-heavy office writes off more
    assert 0.2 <= solo["write_off_share"] <= 0.45
    assert group["write_off_share"] > solo["write_off_share"]


def test_balances_age_correctly(solo_sim) -> None:
    for b in solo_sim.money.balances:
        assert abs(b.balance - (b.current + b.days_31_60 + b.days_61_90 + b.over_90)) < 0.05


def test_most_insured_patients_leave_benefits_unused(solo_sim, group_sim) -> None:
    for sim in (solo_sim, group_sim):
        active = {h.patient_id for h in sim.history if h.status == "active"}
        rows = [
            b for b in sim.money.benefits if b.year == SIM_TODAY.year and b.rank == "primary" and b.patient_id in active
        ]
        assert rows
        avg = sum(b.remaining for b in rows) / len(rows)
        assert 600 <= avg <= 1400
        assert sum(b.remaining >= b.annual_max * 0.5 for b in rows) / len(rows) > 0.5
