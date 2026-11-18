"""Plan the next two weeks for every synced office (make models first).

Usage:
    python -m sadhana.optimizer
    python -m sadhana.optimizer solo_manhattan
"""

import argparse
from datetime import date

from sqlalchemy import text

from sadhana.config import settings
from sadhana.data.db import get_engine
from sadhana.optimizer.run import plan_office
from sadhana.simulator.population import SIM_TODAY


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("offices", nargs="*")
    args = parser.parse_args()
    engine = get_engine()
    with engine.connect() as conn:
        synced = conn.execute(text("SELECT office_id, source FROM sadhana_meta.offices ORDER BY office_id")).all()
    demo = {p.stem for p in (settings.configs_dir / "offices").glob("*.json")}
    wanted = set(args.offices) or demo
    for office_id, source in synced:
        if office_id not in wanted:
            continue
        today = SIM_TODAY if source == "simulator" else date.today()
        s = plan_office(engine, office_id, today).summary()
        cols = ", ".join(f"{n} {c}" for c, n in sorted(s["filled_by_column"].items()))
        print(
            f"\n{office_id} (as of {today})\n"
            f"  open: {s['open_blocks']} blocks, {s['open_hours']} chair hours in the next two weeks\n"
            f"  plan: {s['filled_blocks']} offers ({cols}), expected ${s['expected_value']:,.0f}\n"
            f"  at risk: {s['at_risk_visits']} booked visits, ${s['value_at_risk']:,.0f} expected to be lost"
        )


if __name__ == "__main__":
    main()
