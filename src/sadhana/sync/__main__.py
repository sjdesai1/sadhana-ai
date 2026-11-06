"""Load `make sim` output for one or all demo offices into Postgres.

Usage:
    python -m sadhana.sync                    # every demo office
    python -m sadhana.sync solo_manhattan
"""

import argparse
from pathlib import Path

from sadhana.config import settings
from sadhana.data.db import get_engine
from sadhana.sync.adapters import SimulatorAdapter
from sadhana.sync.run import sync_office


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("offices", nargs="*")
    args = parser.parse_args()
    ids = args.offices or sorted(p.stem for p in (settings.configs_dir / "offices").glob("*.json"))
    engine = get_engine()
    for office_id in ids:
        adapter = SimulatorAdapter(office_id, Path(settings.outputs_dir) / office_id / "sim", settings.configs_dir)
        counts = sync_office(engine, adapter)
        print(f"{office_id}: {sum(counts.values()):,} rows into office_{office_id}")
        print("  " + ", ".join(f"{k} {v:,}" for k, v in counts.items()))


if __name__ == "__main__":
    main()
