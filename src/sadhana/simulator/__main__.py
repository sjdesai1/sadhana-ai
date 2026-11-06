"""Generate the synthetic practice for one or all offices: patients, charts and three years of history.

Usage:
    python -m sadhana.simulator                       # every office, seed 42
    python -m sadhana.simulator solo_manhattan --seed 7
"""

import argparse
import csv
import json
from collections import Counter
from datetime import date
from pathlib import Path

from sadhana.config import settings
from sadhana.simulator import charts, history
from sadhana.simulator.population import SIM_TODAY, generate, write


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("offices", nargs="*")
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()

    offices_dir = settings.configs_dir / "offices"
    ids = args.offices or sorted(p.stem for p in offices_dir.glob("*.json"))
    for office_id in ids:
        office = json.loads((offices_dir / f"{office_id}.json").read_text())
        payers_path = settings.configs_dir / "payers" / f"payers_{office['state']}.csv"
        with payers_path.open(newline="") as f:
            payers = {r["payer"]: r for r in csv.DictReader(f)}
        patients, truths = generate(office, settings.configs_dir, payers, seed=args.seed)
        former, former_truths = history.former_patients(office, settings.configs_dir, payers, patients, args.seed)
        everyone, all_truths = patients + former, truths + former_truths
        out = write(everyone, all_truths, Path(settings.outputs_dir) / office_id / "sim")
        n = len(patients)
        households = len({p.household_id for p in patients})
        kids = sum(p.age < 18 for p in patients)
        print(f"\n{office['name']}: {n} patients in {households} households, plus {len(former)} former -> {out}")
        print(f"  under 18: {kids / n:.0%}   65+: {sum(p.age >= 65 for p in patients) / n:.0%}")
        print(
            "  payers:  " + ", ".join(f"{k} {v / n:.0%}" for k, v in Counter(p.payer for p in patients).most_common())
        )
        print(
            "  languages: "
            + ", ".join(f"{k} {v / n:.0%}" for k, v in Counter(p.language for p in patients).most_common(5))
        )

        teeth, conds, perio, plan = charts.generate(everyone, all_truths, seed=args.seed)
        charts.write(teeth, conds, perio, plan, out)
        current = {p.patient_id for p in patients}
        adults = [p for p in patients if p.age >= 20]
        decay = {c.patient_id for c in conds if c.condition == "caries"}
        decay_share = sum(p.patient_id in decay for p in adults) / len(adults)
        print(f"  charts: {len(teeth)} teeth, adults with active decay {decay_share:.0%}")
        plan = [x for x in plan if x.patient_id in current]
        print(
            f"  treatment plan: {len(plan)} proposed items for {len({x.patient_id for x in plan}) / n:.0%} of patients"
        )

        former_ids = {p.patient_id for p in former}
        appts, appt_truth, hist, flows = history.run(
            office, everyone, all_truths, perio, settings.configs_dir, former_ids, seed=args.seed
        )
        history.write(appts, appt_truth, hist, flows, out)
        _print_history(office, appts, appt_truth, hist, flows, perio)


def _print_history(office, appts, appt_truth, hist, flows, perio) -> None:
    cause = {x.appt_id: x.cause for x in appt_truth}
    year_ago = date(SIM_TODAY.year - 1, SIM_TODAY.month, SIM_TODAY.day).isoformat()
    today = SIM_TODAY.isoformat()
    recent = [a for a in appts if year_ago <= a.date < today and cause[a.appt_id] != "left_practice"]
    status = Counter(a.status for a in recent)
    ahead = sum(a.status == "scheduled" for a in appts)
    print(f"  history: {len(appts)} hygiene appointments over 3 years, {ahead} booked ahead")
    print(
        f"  last 12 months: no-shows {status['no_show'] / len(recent):.1%}, "
        f"late cancels {status['late_cancel'] / len(recent):.1%}, "
        f"hygiene chairs {history.utilization(appts, office):.0%} full"
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


if __name__ == "__main__":
    main()
