"""What payers really allow, learned from paid claims, and payers that pay less than their schedule on file.

The fee schedules in the configs are what an office believes each payer allows. Paid claims say what they really
allow. For each payer and code, the allowed amount used for margins is a blend:
    w x median allowed on recent claims + (1 - w) x the schedule on file,   w = n / (n + k)
so a code with many recent claims follows the claims, and a rare code leans on the schedule, adjusted by how
that payer pays across all its codes.

Each payer also gets a check: the share of its schedule it has paid recently, and the month it changed if it
changed (a cut or a raise of more than a few percent against the three months before). A payer paying well under
its schedule is flagged for the office: update the schedule on file, or take it to the payer.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path
from statistics import median
from typing import Any

from sqlalchemy import Connection, insert, text

from sadhana.data.schema import payer_rates, payer_schedule_check
from sadhana.economics.engine import OfficeInputs, allowed_amount

Row = dict[str, Any]


def load_settings(configs_dir: Path) -> Row:
    return json.loads((configs_dir / "economics.json").read_text())["observed_allowed"]


@dataclass
class PayerRates:
    allowed: dict[tuple[str, str], float] = field(default_factory=dict)
    rates: list[Row] = field(default_factory=list)
    checks: list[Row] = field(default_factory=list)

    def flagged(self) -> set[str]:
        return {c["payer"] for c in self.checks if c["flag"]}


def _month(d: date) -> date:
    return d.replace(day=1)


def learn(conn: Connection, inputs: OfficeInputs, today: date, cfg: Row) -> PayerRates:
    """Allowed amounts to use, per payer and code, and each payer's check, from the office's paid claims."""
    catalog = {p["code"]: p for p in inputs.catalog}
    since = today - timedelta(days=cfg["history_days"])
    rows = conn.execute(
        text(
            "SELECT payer, code, date, allowed FROM ledger WHERE date > :s AND date <= :t AND allowed > 0 "
            "AND payer IS NOT NULL ORDER BY date, id"
        ),
        {"s": since, "t": today},
    ).all()
    configured: dict[tuple[str, str], float | None] = {}

    def schedule(payer: str, code: str) -> float | None:
        key = (payer, code)
        if key not in configured:
            p = inputs.payers.get(payer)
            configured[key] = allowed_amount(catalog[code], p, inputs.office, inputs.medicaid)[0] if p else None
        return configured[key]

    recent_from = today - timedelta(days=cfg["window_days"])
    recent: dict[tuple[str, str], list[float]] = {}
    monthly: dict[str, dict[date, list[float]]] = {}  # payer -> month -> [paid, schedule]
    for payer, code, d, allowed in rows:
        if code not in catalog:
            continue
        sched = schedule(payer, code)
        if not sched:
            continue
        amount = float(allowed)
        m = monthly.setdefault(payer, {}).setdefault(_month(d), [0.0, 0.0])
        m[0] += amount
        m[1] += sched
        if d > recent_from:
            recent.setdefault((payer, code), []).append(amount)

    k = cfg["credibility_k"]
    # how each payer pays against its schedule, across its codes (for codes with few claims)
    paid_sched: dict[str, list[float]] = {}
    for (payer, code), amounts in recent.items():
        ps = paid_sched.setdefault(payer, [0.0, 0.0, 0])
        ps[0] += sum(amounts)
        ps[1] += schedule(payer, code) * len(amounts)
        ps[2] += len(amounts)
    payer_ratio = {p: v[0] / v[1] for p, v in paid_sched.items() if v[1]}
    payer_n = {p: int(v[2]) for p, v in paid_sched.items()}

    out = PayerRates()
    for payer in inputs.payers:
        if payer not in payer_ratio:
            continue
        w_payer = payer_n[payer] / (payer_n[payer] + k)
        ratio = 1 + w_payer * (payer_ratio[payer] - 1)
        for code in catalog:
            sched = schedule(payer, code)
            if not sched:
                continue
            amounts = recent.get((payer, code), [])
            prior = sched * ratio
            w = len(amounts) / (len(amounts) + k)
            used = w * median(amounts) + (1 - w) * prior if amounts else prior
            if abs(used - sched) < 0.005:
                continue
            out.allowed[(payer, code)] = round(used, 2)
            out.rates.append(
                {"payer": payer, "code": code, "schedule": round(sched, 2),
                 "observed": round(median(amounts), 2) if amounts else None, "claims": len(amounts),
                 "allowed_used": round(used, 2)}
            )  # fmt: skip
    for payer, months in sorted(monthly.items()):
        series = sorted(months.items())
        ratios = [(m, v[0] / v[1]) for m, v in series if v[1]]
        change_on, change = None, 0.0
        for i in range(3, len(ratios) - 2):
            before = sum(r for _, r in ratios[i - 3 : i]) / 3
            after = sum(r for _, r in ratios[i : i + 3]) / 3
            if abs(after / before - 1) >= cfg["change_threshold"] and abs(after / before - 1) > abs(change):
                change_on, change = ratios[i][0], after / before - 1
        r = payer_ratio.get(payer)
        if r is None:
            continue
        out.checks.append(
            {
                "payer": payer,
                "paid_vs_schedule": round(r, 4),
                "claims": payer_n[payer],
                "changed_on": change_on,
                "change": round(change, 4) if change_on else None,
                "flag": bool(r < 1 - cfg["flag_below"] and payer_n[payer] >= cfg["min_claims_to_flag"]),
            }
        )
    return out


def save(conn: Connection, office_id: str, rates: PayerRates) -> None:
    conn.execute(text("DELETE FROM payer_rates"))
    conn.execute(text("DELETE FROM payer_schedule_check"))
    if rates.rates:
        conn.execute(insert(payer_rates), [{"office_id": office_id, **r} for r in rates.rates])
    if rates.checks:
        conn.execute(insert(payer_schedule_check), [{"office_id": office_id, **c} for c in rates.checks])


def load(conn: Connection) -> dict[tuple[str, str], float]:
    """The learned allowed amounts saved by the last plan."""
    return {(p, c): float(a) for p, c, a in conn.execute(text("SELECT payer, code, allowed_used FROM payer_rates"))}
