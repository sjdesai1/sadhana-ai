"""Simulator part 5: life events, market scenarios and stress settings.

Life events are generated backward from SIM_TODAY: every household keeps the insurance it has today (so each
office's payer mix still matches), and some get a past that led there:
- cash today, working age: had employer PPO until a job loss
- cash today, 65 to 72: had PPO until retirement (Medicare does not cover dental)
- Medicaid today: some had PPO or cash before a job loss
- PPO today: some switched carriers in January, some were uninsured before a new job

Scenarios (recession, a payer cutting fees, a competitor opening) and stress settings are named in the office
config under simulation.scenarios and simulation.overrides, so demo offices stay at baseline.
"""

from __future__ import annotations

import csv
import json
import random
from bisect import bisect_right
from dataclasses import asdict, dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from sadhana.simulator.population import SIM_TODAY, Patient

Row = dict[str, Any]


def deep_merge(base: Row, extra: Row) -> Row:
    out = dict(base)
    for k, v in extra.items():
        out[k] = deep_merge(out[k], v) if isinstance(v, dict) and isinstance(out.get(k), dict) else v
    return out


def load_clock(configs_dir: Path, office: Row | None = None) -> Row:
    """Simulator settings, with the office's own overrides (stress tests, hidden offices) applied on top."""
    clock = json.loads((configs_dir / "simulator" / "clock.json").read_text())
    if office:
        clock = deep_merge(clock, office.get("simulation", {}).get("overrides", {}))
    return clock


def active_scenarios(office: Row, clock: Row) -> dict[str, Row]:
    names = office.get("simulation", {}).get("scenarios", [])
    return {n: clock["scenarios"][n] for n in names}


def scenario_window(sc: Row) -> tuple[date, date]:
    start = date.fromisoformat(sc["start"])
    months = int(sc.get("months", 999))
    y, m = divmod(start.month - 1 + months, 12)
    return start, date(start.year + y, m + 1, 1) if months < 999 else date.max


def in_window(sc: Row, d: date) -> bool:
    lo, hi = scenario_window(sc)
    return lo <= d < hi


@dataclass
class LifeEvent:
    household_id: str
    date: str
    event: str  # job_loss, retirement, new_job, plan_switch
    from_payer: str
    to_payer: str


class Coverage:
    """Which insurance each patient had on any date."""

    def __init__(self, patients: list[Patient], segments: dict[str, list[tuple[date, str]]]) -> None:
        self.current = {p.patient_id: p.payer for p in patients}
        self.household = {p.patient_id: p.household_id for p in patients}
        self.segments = segments  # household -> [(start, payer)], sorted; last one is today's payer
        self.starts = {h: [s for s, _ in segs] for h, segs in segments.items()}

    def payer_on(self, pid: str, d: date) -> str:
        h = self.household.get(pid, "")
        segs = self.segments.get(h)
        if not segs:
            return self.current[pid]
        i = bisect_right(self.starts[h], d) - 1
        return segs[max(i, 0)][1]

    def changed_on(self, pid: str) -> list[date]:
        segs = self.segments.get(self.household.get(pid, ""), [])
        return [s for s, _ in segs[1:]]


def generate(
    office: Row, patients: list[Patient], configs_dir: Path, start: date, seed: int = 42
) -> tuple[Coverage, list[LifeEvent]]:
    clock = load_clock(configs_dir, office)
    cfg = clock["life_events"]
    scen = active_scenarios(office, clock)
    rng = random.Random(f"events-{office['office_id']}-{seed}")
    with (configs_dir / "payers" / f"payers_{office['state']}.csv").open(newline="") as f:
        ptype = {r["payer"]: r["payer_type"] for r in csv.DictReader(f)}
    mix = office["payer_mix"]
    ppos = [p for p in mix if ptype[p] in ("ppo", "premier")]
    ppo_w = [mix[p] for p in ppos]
    job_factor = max((s.get("job_loss_factor", 1.0) for s in scen.values()), default=1.0)
    recession = [s for s in scen.values() if "job_loss_factor" in s]

    def when(lo: date, hi: date) -> date:
        if recession and rng.random() < 0.6:
            a, b = scenario_window(recession[0])
            lo, hi = max(lo, a), min(hi, b, SIM_TODAY - timedelta(days=30))
        return lo + timedelta(days=rng.randint(0, max(0, (hi - lo).days)))

    heads: dict[str, Patient] = {}
    for p in patients:
        if p.role == "head" or p.household_id not in heads:
            heads[p.household_id] = p
    segments: dict[str, list[tuple[date, str]]] = {}
    events: list[LifeEvent] = []
    late = SIM_TODAY - timedelta(days=60)
    for h, head in heads.items():
        now, kind, age = head.payer, ptype[head.payer], head.age
        if not ppos:
            continue
        r = rng.random()
        if kind == "self_pay" and 22 <= age <= 64 and r < cfg["job_loss_cash_share"] * job_factor:
            before, d, ev = rng.choices(ppos, ppo_w)[0], when(start + timedelta(days=60), late), "job_loss"
        elif kind == "self_pay" and 65 <= age <= 72 and r < cfg["retired_cash_share"]:
            before, d, ev = rng.choices(ppos, ppo_w)[0], when(start + timedelta(days=60), late), "retirement"
        elif kind == "medicaid" and age >= 22 and r < cfg["medicaid_after_job_loss_share"] * job_factor:
            before, d, ev = rng.choices(ppos, ppo_w)[0], when(start + timedelta(days=60), late), "job_loss"
        elif kind in ("ppo", "premier") and r < cfg["new_job_from_cash_share"] and "Cash" in mix:
            before, d, ev = "Cash", when(start + timedelta(days=60), late), "new_job"
        elif kind in ("ppo", "premier") and r < cfg["new_job_from_cash_share"] + 3 * cfg["january_switch_per_year"]:
            others = [p for p in ppos if p != now] or ppos
            before = rng.choices(others, [mix[p] for p in others])[0]
            d, ev = date(rng.choice(range(start.year + 1, SIM_TODAY.year + 1)), 1, 1), "plan_switch"
        else:
            continue
        segments[h] = [(date.min, before), (d, now)]
        events.append(LifeEvent(h, d.isoformat(), ev, before, now))
    return Coverage(patients, segments), sorted(events, key=lambda e: (e.date, e.household_id))


def write(events: list[LifeEvent], out_dir: Path) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    with (out_dir / "life_events.csv").open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(LifeEvent.__dataclass_fields__))
        writer.writeheader()
        writer.writerows(asdict(e) for e in events)
    return out_dir
