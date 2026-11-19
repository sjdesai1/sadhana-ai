"""Run the scorecard (slow: it simulates and runs the whole product for every office and scenario).

Usage:
    python -m sadhana.evals
    python -m sadhana.evals group_queens holdout_ues_boutique
    python -m sadhana.evals --seed 7
"""

import argparse
import time
from pathlib import Path

from sadhana.config import settings
from sadhana.data.db import get_engine
from sadhana.evals.scorecard import load_settings, run_one, save


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("labels", nargs="*", help="runs to do (default: all in configs/evals.json)")
    parser.add_argument("--seed", type=int, default=None)
    args = parser.parse_args()
    cfg = load_settings(settings.configs_dir)
    runs = [r for r in cfg["runs"] if not args.labels or r["label"] in args.labels]
    engine = get_engine()
    out = Path(settings.outputs_dir) / "evals"
    results = []
    for run in runs:
        t0 = time.time()
        try:
            r = run_one(engine, run, cfg, out, args.seed)
        except Exception as e:  # noqa: BLE001  a crash is a failed run, recorded with the rest
            r = {"label": run["label"], "office": run["office"], "seed": args.seed or cfg["seed"], "passed": False,
                 "error": f"{type(e).__name__}: {e}", "checks": [], "info": {}, "metrics": {}}  # fmt: skip
            print(f"{run['label']}: CRASH {r['error']}")
            results.append(r)
            continue
        failed = [c["name"] for c in r["checks"] if c["status"] == "fail"]
        print(f"{r['label']}: {'pass' if r['passed'] else 'FAIL'} ({time.time() - t0:.0f}s)"
              + (f"; failed: {', '.join(failed)}" if failed else ""))  # fmt: skip
        results.append(r)
    path = save(engine, results, out)
    print(f"\n{sum(r['passed'] for r in results)} of {len(results)} runs pass every check. Scorecard: {path}")


if __name__ == "__main__":
    main()
