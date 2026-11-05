"""Build economics tables for one or all offices.

Usage:
    python -m sadhana.economics              # every office in configs/offices
    python -m sadhana.economics solo_manhattan
"""

import sys

from sadhana.config import settings
from sadhana.economics.engine import build, load_inputs, write_outputs


def main(argv: list[str]) -> None:
    offices_dir = settings.configs_dir / "offices"
    ids = argv or sorted(p.stem for p in offices_dir.glob("*.json"))
    for office_id in ids:
        inputs = load_inputs(offices_dir / f"{office_id}.json", settings.configs_dir)
        result = build(inputs)
        out = write_outputs(result, settings.outputs_dir)
        ranked = sorted(
            (r for r in result.summary if r["expected_margin_per_chair_hour"] is not None),
            key=lambda r: -r["expected_margin_per_chair_hour"],
        )
        print(f"\n{inputs.office['name']} -> {out}")
        for r in ranked[:10]:
            print(
                f"  {r['code']:6} {r['name'][:38]:38} {r['best_provider']:3} "
                f"${r['expected_margin_per_chair_hour']:>8,.0f}/chair hr"
            )


if __name__ == "__main__":
    main(sys.argv[1:])
