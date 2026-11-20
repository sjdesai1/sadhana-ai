"""Write the monthly report for every synced demo office (make outreach first).

Usage:
    python -m sadhana.reports
    python -m sadhana.reports solo_manhattan
"""

import argparse
import json
from datetime import date
from pathlib import Path

from sqlalchemy import text

from sadhana.config import settings
from sadhana.data.db import get_engine
from sadhana.reports.html import render
from sadhana.reports.monthly import build_report
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
        report = build_report(engine, office_id, today)
        out = Path(settings.outputs_dir) / "reports" / office_id
        out.mkdir(parents=True, exist_ok=True)
        (out / "report.html").write_text(render(report))
        (out / "report.json").write_text(json.dumps(report.as_dict(), indent=2, default=str) + "\n")
        print(f"\n{office_id}: recovered ${report.recovered_total:,.0f} for ${report.fee:,.0f} "
              f"({report.roi:.0f}x); margin ${report.margin_total:,.0f}")  # fmt: skip
        for r in report.recovered:
            print(f"  {r['source']}: {r['extra_bookings']:,.0f} extra visits, ${r['collected']:,.0f}")
        print(f"  front desk time saved: {report.desk['hours_saved']:.0f} hours\n  report: {out / 'report.html'}")


if __name__ == "__main__":
    main()
