"""Train and score the nightly models for every synced office (make sync first).

Usage:
    python -m sadhana.models
    python -m sadhana.models solo_manhattan
"""

import argparse
from datetime import date

from sqlalchemy import text

from sadhana.config import settings
from sadhana.data.db import get_engine
from sadhana.models.run import run_office
from sadhana.simulator.population import SIM_TODAY


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("offices", nargs="*")
    args = parser.parse_args()
    engine = get_engine()
    with engine.connect() as conn:
        synced = conn.execute(text("SELECT office_id, source FROM sadhana_meta.offices ORDER BY office_id")).all()
    demo = {p.stem for p in (settings.configs_dir / "offices").glob("*.json")}
    wanted = set(args.offices) or demo  # hidden test offices only when named
    for office_id, source in synced:
        if office_id not in wanted:
            continue
        today = SIM_TODAY if source == "simulator" else date.today()
        print(f"\n{office_id} (as of {today})")
        for r in run_office(engine, office_id, today):
            m = r.metrics()
            print(
                f"  {r.model}: AUC {m['auc']:.3f}, calibration error {m['calibration_error']:.3f}, "
                f"base rate {m['base_rate']:.1%}, tested on {m['n_test']:,} (trained on {m['n_train']:,}); "
                f"scored {len(r.scores):,}"
            )
            print("    top features: " + ", ".join(n for n, _ in m["top_features"][:5]))


if __name__ == "__main__":
    main()
