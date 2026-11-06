"""Generate the synthetic patient population for one or all offices.

Usage:
    python -m sadhana.simulator                       # every office, seed 42
    python -m sadhana.simulator solo_manhattan --seed 7
"""

import argparse
import csv
import json
from collections import Counter
from pathlib import Path

from sadhana.config import settings
from sadhana.simulator import charts
from sadhana.simulator.population import generate, write


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
        out = write(patients, truths, Path(settings.outputs_dir) / office_id / "sim")
        n = len(patients)
        households = len({p.household_id for p in patients})
        kids = sum(p.age < 18 for p in patients)
        print(f"\n{office['name']}: {n} patients in {households} households -> {out}")
        print(f"  under 18: {kids / n:.0%}   65+: {sum(p.age >= 65 for p in patients) / n:.0%}")
        print(
            "  payers:  " + ", ".join(f"{k} {v / n:.0%}" for k, v in Counter(p.payer for p in patients).most_common())
        )
        print(
            "  languages: "
            + ", ".join(f"{k} {v / n:.0%}" for k, v in Counter(p.language for p in patients).most_common(5))
        )

        teeth, conds, perio, plan = charts.generate(patients, truths, seed=args.seed)
        charts.write(teeth, conds, perio, plan, out)
        adults = [p for p in patients if p.age >= 20]
        decay = {c.patient_id for c in conds if c.condition == "caries"}
        decay_share = sum(p.patient_id in decay for p in adults) / len(adults)
        print(f"  charts: {len(teeth)} teeth, adults with active decay {decay_share:.0%}")
        print(
            f"  treatment plan: {len(plan)} proposed items for {len({x.patient_id for x in plan}) / n:.0%} of patients"
        )


if __name__ == "__main__":
    main()
