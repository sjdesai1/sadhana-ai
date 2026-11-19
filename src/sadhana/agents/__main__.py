"""Run a simulated outreach pilot for every synced office (make optimize first), then measure the lift.

Usage:
    python -m sadhana.agents
    python -m sadhana.agents solo_manhattan --days 10
"""

import argparse
from datetime import date
from pathlib import Path

from sqlalchemy import text

from sadhana.agents.run import run_outreach
from sadhana.config import settings
from sadhana.data.db import get_engine
from sadhana.optimizer.plan import load_settings as optimizer_settings
from sadhana.simulator.population import SIM_TODAY


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("offices", nargs="*")
    parser.add_argument("--days", type=int, default=20, help="send days to run")
    args = parser.parse_args()
    engine = get_engine()
    with engine.connect() as conn:
        synced = conn.execute(text("SELECT office_id, source FROM sadhana_meta.offices ORDER BY office_id")).all()
    demo = {p.stem for p in (settings.configs_dir / "offices").glob("*.json")}
    wanted = set(args.offices) or demo
    assumed = optimizer_settings(settings.configs_dir)["outreach_lift"]
    for office_id, source in synced:
        if office_id not in wanted:
            continue
        sim = source == "simulator"
        today = SIM_TODAY if sim else date.today()
        sim_dir = Path(settings.outputs_dir) / office_id / "sim" if sim else None
        r = run_outreach(engine, office_id, today, args.days, sim_dir)
        print(f"\n{office_id}: {len(r.days)} send days from {r.days[0]} to {r.days[-1]}")
        print("  targets: " + ", ".join(f"{k} {v:,}" for k, v in sorted(r.by_status.items())))
        print("  messages: " + ", ".join(f"{k} {v:,}" for k, v in sorted(r.by_channel.items())))
        print(f"  opt-outs: {r.opt_outs}")
        for e in r.estimates:
            note = f" (optimizer assumed {assumed[e['lift_group']]:.0%})" if e["lift_group"] in assumed else ""
            print(
                f"  lift {e['lift_group']}: {e['lift']:.1%} [{e['ci_low']:.1%} to {e['ci_high']:.1%}]{note}; "
                f"outreach {e['rate_outreach']:.1%} of {e['n_outreach']:,} vs holdout {e['rate_holdout']:.1%} "
                f"of {e['n_holdout']:,}"
            )


if __name__ == "__main__":
    main()
