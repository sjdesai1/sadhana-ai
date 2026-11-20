"""The 90-day plan: week by week for the next 13 weeks, per column (hygiene, doctor, specialist).

- Capacity: chair hours from the office calendar (hours, lunch, who works which day, holidays)
- Booked: what is on the books now, including the agents' bookings and cancellations not yet in the PMS
- Expected: hours the office will really fill with its usual booking. The share of capacity it filled over the
  last eight weeks, never below what is already booked times its recent show rate. Backtested on past quarters
  (see `backtest`): about 5% weekly error for hygiene, and the quarter total within a few percent
- Outreach: on top of that, recall outreach and treatment follow-up add bookings at the measured lift (or the
  assumption until it is measured), spread over the coming weeks and capped by the open hours each week
- Value: hours x the office's margin per chair hour in that column over the last year; treatment follow-up at
  each item's own margin

So the plan says where the next three months of margin come from (the books, recall, treatment follow-up),
which weeks will still have empty chairs, and how much insurance resets on Dec 31.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any

import numpy as np
import pandas as pd
from sqlalchemy import Connection, Engine, insert, text

from sadhana.agents import lift
from sadhana.config import settings
from sadhana.data.db import office_session
from sadhana.data.schema import plan_90
from sadhana.economics import observed
from sadhana.office_calendar import Calendar, holidays
from sadhana.optimizer.plan import Planner

Row = dict[str, Any]
COLUMNS = ("hygiene", "doctor", "specialist")


def _week(d: date) -> date:
    return d - timedelta(days=d.weekday())


def _appointments(conn: Connection) -> pd.DataFrame:
    a = pd.DataFrame(
        conn.execute(
            text(
                'SELECT appt_id, patient_id, provider_id, "column", date, start, "end", booked_on, status, status_on '
                "FROM appointments"
            )
        )
        .mappings()
        .all()
    )
    a["hours"] = [
        (e.hour * 60 + e.minute - s.hour * 60 - s.minute) / 60 for s, e in zip(a["start"], a["end"], strict=True)
    ]
    a["week"] = [_week(d) for d in a["date"]]
    return a


class Forecaster:
    """Baseline weekly chair hours, from the schedule as it stood on `as_of` (point in time, so it can be
    backtested on the past)."""

    def __init__(self, appts: pd.DataFrame, cal: Calendar, cfg: Row) -> None:
        self.a, self.cal, self.cfg = appts, cal, cfg
        self.done = appts[appts["status"] == "completed"].groupby(["week", "column"])["hours"].sum()
        self._cap: dict[tuple[date, str], float] = {}

    def capacity(self, week: date, column: str) -> float:
        key = (week, column)
        if key not in self._cap:
            self._cap[key] = sum(self.cal.capacity(week + timedelta(days=i), column) for i in range(7)) / 60
        return self._cap[key]

    def booked_as_of(self, as_of: date) -> pd.Series:
        a = self.a
        alive = (a["booked_on"] <= as_of) & ~((a["status"] == "cancelled") & (a["status_on"] <= as_of))
        return a[alive & (a["date"] > as_of)].groupby(["week", "column"])["hours"].sum()

    def show_rate(self, as_of: date, column: str) -> float:
        a = self.a
        since = as_of - timedelta(days=30 * self.cfg["show_rate_months"])
        past = a[(a["date"] <= as_of) & (a["date"] > since) & (a["column"] == column)]
        kept = past.loc[past["status"] == "completed", "hours"].sum()
        due = past.loc[past["status"].isin(("completed", "no_show", "late_cancel")), "hours"].sum()
        return float(kept / due) if due else 0.85

    def trailing_share(self, as_of: date, column: str) -> float:
        shares = []
        this_week = _week(as_of)
        for j in range(1, self.cfg["trailing_weeks"] + 1):
            wk = this_week - timedelta(weeks=j)
            cap = self.capacity(wk, column)
            if cap:
                shares.append(self.done.get((wk, column), 0.0) / cap)
        return float(np.mean(shares)) if shares else 0.0

    def weeks(self, as_of: date) -> list[date]:
        """The next full weeks, starting the Monday after `as_of`."""
        first = _week(as_of) + timedelta(weeks=1)
        return [first + timedelta(weeks=k) for k in range(self.cfg["weeks"])]

    def baseline(self, as_of: date, extra_booked: dict[tuple[date, str], float] | None = None) -> pd.DataFrame:
        """`extra_booked`: hours the agents booked (or cancelled, negative) that the PMS does not show yet."""
        booked = self.booked_as_of(as_of)
        extra_booked = extra_booked or {}
        rows = []
        for column in COLUMNS:
            share, show = self.trailing_share(as_of, column), self.show_rate(as_of, column)
            for wk in self.weeks(as_of):
                cap = self.capacity(wk, column)
                b = max(0.0, float(booked.get((wk, column), 0.0)) + extra_booked.get((wk, column), 0.0))
                expected = min(cap, max(b * show, share * cap)) if cap else 0.0
                rows.append({"week": wk, "column": column, "capacity_hours": cap, "booked_hours": b,
                             "expected_hours": expected})  # fmt: skip
        return pd.DataFrame(rows)

    def actual(self, weeks: list[date]) -> pd.DataFrame:
        return pd.DataFrame(
            [
                {"week": wk, "column": c, "actual_hours": float(self.done.get((wk, c), 0.0))}
                for wk in weeks
                for c in COLUMNS
            ]
        )


def backtest(engine: Engine, office_id: str, as_of: date) -> pd.DataFrame:
    """The baseline forecast made on a past date, next to what the office really did."""
    office, f = _setup(engine, office_id, as_of)
    out = f.baseline(as_of).merge(f.actual(f.weeks(as_of)), on=["week", "column"])
    return out[out["week"] + timedelta(days=7) <= _latest(f)]


def _latest(f: Forecaster) -> date:
    return max(f.a.loc[f.a["status"] == "completed", "date"])


def _setup(engine: Engine, office_id: str, today: date) -> tuple[Row, Forecaster]:
    with engine.connect() as conn:
        office = conn.execute(
            text("SELECT config FROM sadhana_meta.offices WHERE office_id = :o"), {"o": office_id}
        ).scalar_one()
    planner_cfg = Planner(office, settings.configs_dir, today).cfg
    cal = Calendar(office, holidays(range(today.year - 4, today.year + 2)))
    with office_session(engine, office_id) as conn:
        appts = _appointments(conn)
    return office, Forecaster(appts, cal, planner_cfg["forecast"])


@dataclass
class Plan90:
    office_id: str
    today: date
    weeks: pd.DataFrame
    benefits_expiring: float
    benefits_patients: int
    lifts: dict[str, float] = field(default_factory=dict)
    lift_source: dict[str, str] = field(default_factory=dict)

    def summary(self) -> Row:
        w = self.weeks
        cap = w["capacity_hours"].sum()
        filled = w["expected_hours"].sum()
        added = (w["recall_hours"] + w["treatment_hours"]).sum()
        worst = w.groupby("week")["unfilled_hours"].sum().sort_values(ascending=False).head(3)
        return {
            "as_of": self.today.isoformat(),
            "weeks": len(w["week"].unique()),
            "capacity_hours": round(cap, 1),
            "expected_hours": round(filled, 1),
            "outreach_hours": round(added, 1),
            "unfilled_hours": round(w["unfilled_hours"].sum(), 1),
            "utilization": round(filled / cap, 3) if cap else 0.0,
            "utilization_with_outreach": round((filled + added) / cap, 3) if cap else 0.0,
            "expected_value": round(w["expected_value"].sum(), 2),
            "outreach_value": round(w["outreach_value"].sum(), 2),
            "emptiest_weeks": [{"week": k.isoformat(), "unfilled_hours": round(v, 1)} for k, v in worst.items()],
            "benefits_expiring": round(self.benefits_expiring, 2),
            "benefits_patients": self.benefits_patients,
            "outreach_lift": self.lifts,
            "lift_source": self.lift_source,
        }


def _margin_per_hour(conn: Connection, planner: Planner, today: date) -> dict[str, float]:
    """Margin per completed chair hour in each column over the last year."""
    rows = conn.execute(
        text(
            'SELECT a.appt_id, a."column", a.provider_id, a.start, a."end", p.payer, pr.code FROM appointments a '
            "JOIN patients p USING (patient_id) LEFT JOIN procedures pr ON pr.appt_id = a.appt_id "
            "WHERE a.status = 'completed' AND a.date > :d"
        ),
        {"d": today - timedelta(days=365)},
    ).all()
    money: dict[str, float] = {}
    hours: dict[str, dict[str, float]] = {}
    for appt, column, provider, start, end, payer, code in rows:
        hours.setdefault(column, {})[appt] = (end.hour * 60 + end.minute - start.hour * 60 - start.minute) / 60
        if code and code in planner.catalog:
            money[column] = money.get(column, 0.0) + planner._margin(code, payer or "", provider)
    return {c: money.get(c, 0.0) / sum(h.values()) for c, h in hours.items() if sum(h.values())}


def forecast_office(engine: Engine, office_id: str, today: date) -> Plan90:
    office, f = _setup(engine, office_id, today)
    with office_session(engine, office_id) as conn:
        learned = observed.load(conn)
    planner = Planner(office, settings.configs_dir, today, learned)
    cfg = planner.cfg["forecast"]
    measured = lift.latest(engine, office_id, planner.cfg["min_holdout_for_lift"])
    lifts = {**planner.cfg["outreach_lift"], **measured}
    source = {k: ("measured" if k in measured else "assumed") for k in lifts}
    with office_session(engine, office_id) as conn:
        pending = conn.execute(
            text('SELECT kind, date, minutes, "column" FROM pending_bookings WHERE date > :t'), {"t": today}
        ).all()
        recall = conn.execute(
            text(
                "SELECT s.patient_id, sc.score, s.last_cleaning, coalesce(pe.maintenance, false) FROM patient_status s "
                "LEFT JOIN scores sc ON sc.entity_id = s.patient_id AND sc.model = 'recall_self_book' "
                "LEFT JOIN perio pe ON pe.patient_id = s.patient_id WHERE s.next_cleaning IS NULL "
                "AND s.last_cleaning IS NOT NULL AND (sc.score IS NOT NULL OR s.status = 'active') "
                "ORDER BY s.patient_id"
            )
        ).all()
        items = conn.execute(
            text(
                "SELECT t.plan_id, t.code, p.payer, sc.score FROM treatment_plan t JOIN patients p USING (patient_id) "
                "JOIN scores sc ON sc.entity_id = t.plan_id AND sc.model = 'treatment_accept' "
                "WHERE t.status IN ('proposed', 'accepted') ORDER BY t.plan_id"
            )
        ).all()
        benefits = conn.execute(
            text("SELECT coalesce(sum(benefits_this_year), 0), count(*) FROM next_best WHERE benefits_this_year > 0")
        ).one()
        per_hour = _margin_per_hour(conn, planner, today)

    changes: dict[tuple[date, str], float] = {}
    for kind, d, minutes, column in pending:
        key = (_week(d), column)
        changes[key] = changes.get(key, 0.0) + (minutes / 60) * (1 if kind == "book" else -1)
    w = f.baseline(today, changes)
    weeks = f.weeks(today)
    w["open_hours"] = (w["capacity_hours"] - w["expected_hours"]).clip(lower=0)

    # demand outreach adds: incremental bookings at the measured lift, by week and column
    demand: dict[tuple[date, str], list[tuple[float, float, str]]] = {}  # (hours, value, kind)

    def add(week_idx: int, column: str, hours: float, value: float, kind: str) -> None:
        wk = weeks[min(max(week_idx, 0), len(weeks) - 1)]
        demand.setdefault((wk, column), []).append((hours, value, kind))

    mean_self = float(np.mean([s for _, s, _, _ in recall if s is not None]) or 0.15) if recall else 0.15
    hyg_rate = per_hour.get("hygiene", 0.0)
    for i, (_, score, last, perio) in enumerate(recall):
        due = last + timedelta(days=91 if perio else 182)
        p_self = float(score) if score is not None else mean_self
        gain = lifts["recall"] * (1 - p_self)
        if due <= today and score is not None:  # overdue now, on the outreach list
            idx = i % cfg["recall_spread_weeks"]
        elif today < due < today + timedelta(weeks=cfg["weeks"]):  # coming due: outreach in the week it is due
            idx = (due - weeks[0]).days // 7
        else:
            continue
        add(idx, "hygiene", gain * 1.0, gain * 1.0 * hyg_rate, "recall")
    for i, (_, code, _payer, score) in enumerate(items):
        if code not in planner.catalog:
            continue
        gain = lifts["treatment"] * (1 - float(score))
        column = planner._column(code)
        hours = planner._minutes(code) / 60
        margin = planner.fallback.get(code, 0.0)
        add(i % cfg["treatment_spread_weeks"], column, gain * hours, gain * margin, "treatment")

    # fill each week's open hours in order; what does not fit spills to the next week
    w = w.set_index(["week", "column"]).sort_index()
    w["recall_hours"] = 0.0
    w["treatment_hours"] = 0.0
    w["outreach_value"] = 0.0
    for column in COLUMNS:
        carry: list[tuple[float, float, str]] = []
        for wk in weeks:
            room = float(w.loc[(wk, column), "open_hours"])
            queue = carry + demand.get((wk, column), [])
            carry = []
            for hours, value, kind in queue:
                if hours <= room:
                    room -= hours
                    w.loc[(wk, column), f"{kind}_hours"] += hours
                    w.loc[(wk, column), "outreach_value"] += value
                elif room > 0:
                    share = room / hours
                    w.loc[(wk, column), f"{kind}_hours"] += room
                    w.loc[(wk, column), "outreach_value"] += value * share
                    carry.append((hours - room, value * (1 - share), kind))
                    room = 0.0
                else:
                    carry.append((hours, value, kind))
    w = w.reset_index()
    w["unfilled_hours"] = (w["open_hours"] - w["recall_hours"] - w["treatment_hours"]).clip(lower=0)
    w["expected_value"] = [h * per_hour.get(c, 0.0) for h, c in zip(w["expected_hours"], w["column"], strict=True)]
    w = w[w["capacity_hours"] > 0].reset_index(drop=True)
    plan = Plan90(office_id, today, w, float(benefits[0]), int(benefits[1]), lifts, source)

    with office_session(engine, office_id) as conn:
        conn.execute(text("DELETE FROM plan_90"))
        cols = ["week", "column", "capacity_hours", "booked_hours", "expected_hours", "open_hours", "recall_hours",
                "treatment_hours", "unfilled_hours", "expected_value", "outreach_value"]  # fmt: skip
        records = [
            {
                "office_id": office_id,
                **{k: (round(float(v), 2) if k not in ("week", "column") else v) for k, v in r.items()},
            }
            for r in w[cols].to_dict("records")
        ]
        if records:
            conn.execute(insert(plan_90), records)
    return plan
