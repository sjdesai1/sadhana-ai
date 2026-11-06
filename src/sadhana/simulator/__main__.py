"""Generate the synthetic practice for one or all offices: patients, charts and three years of history.

Usage:
    python -m sadhana.simulator                       # every office, seed 42
    python -m sadhana.simulator solo_manhattan --seed 7
"""

import argparse
from collections import Counter
from datetime import date
from pathlib import Path

from sadhana.config import settings
from sadhana.simulator import history, money
from sadhana.simulator.build import Sim, simulate, write
from sadhana.simulator.population import SIM_TODAY
from sadhana.simulator.treatment import load_catalog, plan_value


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("offices", nargs="*")
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()

    configs = settings.configs_dir
    ids = args.offices or sorted(p.stem for p in (configs / "offices").glob("*.json"))
    for office_id in ids:
        sim = simulate(office_id, configs, seed=args.seed)
        out = write(sim, Path(settings.outputs_dir) / office_id / "sim")
        _print_population(sim, out)
        _print_history(sim.office, sim.appointments, sim.appointment_truth, sim.history, sim.flows, sim.perio)
        _print_treatment(sim, configs)
        _print_money(sim)


def _print_population(sim: Sim, out: Path) -> None:
    patients = [p for p in sim.patients if p.patient_id in sim.current_ids]
    n = len(patients)
    households = len({p.household_id for p in patients})
    print(
        f"\n{sim.office['name']}: {n} patients in {households} households, plus {len(sim.former_ids)} former -> {out}"
    )
    kids, seniors = sum(p.age < 18 for p in patients), sum(p.age >= 65 for p in patients)
    print(f"  under 18: {kids / n:.0%}   65+: {seniors / n:.0%}")
    print("  payers:  " + ", ".join(f"{k} {v / n:.0%}" for k, v in Counter(p.payer for p in patients).most_common()))
    languages = Counter(p.language for p in patients).most_common(5)
    print("  languages: " + ", ".join(f"{k} {v / n:.0%}" for k, v in languages))
    adults = [p for p in patients if p.age >= 20]
    decay = {c.patient_id for c in sim.conditions[: sim.chart_conditions] if c.condition == "caries"}
    share = sum(p.patient_id in decay for p in adults) / len(adults)
    print(f"  charts: {len(sim.teeth)} teeth, adults with untreated decay at their first exam {share:.0%}")


def _print_history(office, appts, appt_truth, hist, flows, perio) -> None:
    cause = {x.appt_id: x.cause for x in appt_truth}
    year_ago = date(SIM_TODAY.year - 1, SIM_TODAY.month, SIM_TODAY.day).isoformat()
    today = SIM_TODAY.isoformat()
    recent = [a for a in appts if year_ago <= a.date < today and cause[a.appt_id] != "left_practice"]
    status = Counter(a.status for a in recent)
    ahead = sum(a.status == "scheduled" for a in appts)
    columns = Counter(a.column for a in appts)
    print(f"  history: {len(appts)} appointments over 3 years ({dict(columns)}), {ahead} booked ahead")
    full = ", ".join(
        f"{c} {history.utilization(appts, office, c):.0%}" for c in ("hygiene", "doctor", "specialist") if columns[c]
    )
    print(
        f"  last 12 months: no-shows {status['no_show'] / len(recent):.1%}, "
        f"late cancels {status['late_cancel'] / len(recent):.1%}, chairs full: {full}"
    )
    new = sum(a.kind == "new_patient" and a.status == "completed" for a in appts)
    left = [f for f in flows if f.left_on]
    seen = Counter(h.status for h in hist)
    print(
        f"  patients: {new / 36:.0f} new a month, {len(left)} left "
        f"({sum(f.office_knows for f in left)} told the office), "
        f"{seen['active']} active, {seen['inactive']} inactive"
    )
    print(f"  recall: {history.overdue_share(hist, flows, perio):.0%} of active patients overdue with nothing booked")


def _print_treatment(sim: Sim, configs: Path) -> None:
    appts, flows = sim.appointments, sim.flows
    value = plan_value(sim.plan, sim.office, load_catalog(configs), sim.current_ids)
    total = sum(value.values())
    order = ("completed", "scheduled", "accepted", "proposed", "declined", "referred")
    print("  treatment diagnosed: " + ", ".join(f"{s} ${value.get(s, 0) / 1000:,.0f}k" for s in order))
    open_value = value.get("accepted", 0) + value.get("proposed", 0)
    emergencies = sum(a.kind == "emergency" and a.status == "completed" for a in appts)
    turned = sum(f.emergencies_turned_away for f in flows)
    print(
        f"  unscheduled treatment: ${open_value / 1000:,.0f}k ({open_value / total:.0%} of diagnosed); "
        f"emergencies seen {emergencies}, turned away {turned}"
    )


def _print_money(sim: Sim) -> None:
    if sim.money is None:
        return
    m = money.summary(sim.money)
    print(
        f"  money, last 12 months: gross ${m['gross'] / 1000:,.0f}k, write-offs {m['write_off_share']:.0%}, "
        f"net ${m['net'] / 1000:,.0f}k, collected {m['collection_rate']:.0%} of net"
    )
    print(
        f"  claims: {m['first_pass_denial']:.1%} denied first time, paid in {m['median_days_to_pay']} days (median); "
        f"patient balances ${m['patient_ar'] / 1000:,.0f}k "
        f"({m['over_90'] / max(m['patient_ar'], 1):.0%} over 90 days), "
        f"insurance owed ${m['insurance_pending'] / 1000:,.0f}k"
    )
    active = {h.patient_id for h in sim.history if h.status == "active"}
    this_year = [
        b for b in sim.money.benefits if b.year == SIM_TODAY.year and b.rank == "primary" and b.patient_id in active
    ]
    unused = sum(b.remaining for b in this_year)
    print(
        f"  benefits {SIM_TODAY.year}: {len(this_year)} active insured patients, ${unused / 1000:,.0f}k unused "
        f"(${unused / max(len(this_year), 1):,.0f} each), expiring Dec 31"
    )


if __name__ == "__main__":
    main()
