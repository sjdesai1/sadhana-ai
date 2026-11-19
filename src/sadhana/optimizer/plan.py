"""The slot plan: who should fill each open slot in the next two weeks, and which booked visits are at risk.

For every open stretch in the schedule, every candidate who fits it is valued as
    chance they book if offered x chance they show x the office's margin on that work, by that provider
where "if offered" adds an assumed outreach lift to the models' baseline (until randomized holdouts measure it),
and margin comes from the economics engine, so doctor hours go to doctor-worthy work. Urgent work is weighted up
for ranking. An assignment solver then gives each block its best candidate without using anyone twice.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from scipy.optimize import linear_sum_assignment

from sadhana.economics.engine import build, can_perform, inputs_for
from sadhana.office_calendar import WINDOWS, Calendar, hhmm, holidays, to_min

Row = dict[str, Any]
SRP = {"D4341", "D4342", "D4355"}
ROOT_CANAL = {"D3310", "D3320", "D3330"}
COSMETIC = {"D9972", "D8090", "D2962"}


@dataclass
class Fill:
    date: str
    provider_id: str
    column: str
    start: str
    minutes: int
    patient_id: str
    plan_id: str  # empty for a cleaning
    code: str
    p_book: float
    p_show: float
    margin: float
    expected_value: float
    reason: str


@dataclass
class AtRisk:
    appt_id: str
    patient_id: str
    date: str
    start: str
    provider_id: str
    p_missed: float
    value_at_risk: float
    action: str
    backup_patient_id: str
    backup_code: str


def load_settings(configs_dir: Path) -> Row:
    return json.loads((configs_dir / "optimizer.json").read_text())


class Planner:
    def __init__(self, office: Row, configs_dir: Path, today: date) -> None:
        self.office, self.today = office, today
        self.cfg = load_settings(configs_dir)
        self.lift_source = {k: "assumed" for k in self.cfg["outreach_lift"]}
        inputs = inputs_for(office, configs_dir)
        self.catalog = {r["code"]: r for r in inputs.catalog}
        econ = build(inputs)
        self.margin = {(r["code"], r["payer"], r["provider_id"]): float(r["net_margin"]) for r in econ.detail}
        self.fallback = {r["code"]: float(r["expected_margin"] or 0) for r in econ.summary}
        self.cal = Calendar(office, holidays(range(today.year, today.year + 2)))
        self.staff = {s["id"]: s for s in office["staff"]}
        self.eligible = {
            code: {s["id"] for s in office["staff"] if can_perform(s, proc, office)[0]}
            for code, proc in self.catalog.items()
        }

    # schedule

    def blocks(self, booked: pd.DataFrame) -> list[Row]:
        """Open stretches per provider in the horizon, after what is already booked."""
        for r in booked.itertuples():
            self.cal.book(r.date, r.provider_id, to_min(r.start), to_min(r.end) - to_min(r.start))
        cfg = self.cfg
        out = []
        for i in range(cfg["min_lead_days"], cfg["horizon_days"] + 1):
            d = self.today + timedelta(days=i)
            for column in ("hygiene", "doctor", "specialist"):
                for pid in self.cal.providers(d, column):
                    for start, usable in self.cal.gaps(d, pid, cfg["min_block_minutes"]):
                        start += -start % 10
                        usable -= start % 10
                        out.append({"date": d, "provider_id": pid, "column": column, "start": start, "minutes": usable})
        return out

    # candidates

    def _margin(self, code: str, payer: str, provider: str) -> float:
        return self.margin.get((code, payer, provider), self.fallback.get(code, 0.0))

    def _column(self, code: str) -> str:
        specialists = {s["id"] for s in self.cal.columns["specialist"]}
        hygienists = {s["id"] for s in self.cal.columns["hygiene"]}
        ok = self.eligible.get(code, set())
        if ok & specialists:
            return "specialist"
        if code in SRP and ok & hygienists:
            return "hygiene"
        return "doctor"

    def _minutes(self, code: str) -> int:
        row = self.catalog[code]
        visits = int(row["visits"])
        return max(20, int(row["chair_min"]) - 20 * (visits - 1))

    def candidates(
        self,
        patients: pd.DataFrame,
        recall: pd.DataFrame,
        plan: pd.DataFrame,
        accept: pd.DataFrame,
        miss_rate: dict[str, float],
        busy_patients: set[str],
    ) -> list[Row]:
        cfg = self.cfg
        lift = cfg["outreach_lift"]
        info = patients.set_index("patient_id")
        out: list[Row] = []
        for r in recall.itertuples():
            pid = r.patient_id
            if pid in busy_patients or pid not in info.index:
                continue
            p = info.loc[pid]
            age = (self.today - p["birth_date"].date()).days / 365.25 if pd.notna(p["birth_date"]) else 30
            code = "D4910" if p["perio_maintenance"] else ("D1120" if age < 14 else "D1110")
            out.append(
                {
                    "patient_id": pid, "plan_id": "", "code": code, "column": "hygiene",
                    "minutes": 45 if code == "D1120" else 60, "priority": "needed",
                    "p_book": float(r.score) + (1 - float(r.score)) * lift["recall"],
                    "reason": "overdue for a cleaning",
                }
            )  # fmt: skip
        open_items = plan[plan["status"].isin(("proposed", "accepted"))]
        chance = dict(zip(accept["entity_id"], accept["score"], strict=True))
        blocked = {
            (r.patient_id, r.tooth) for r in open_items.itertuples() if r.code in ROOT_CANAL or r.code == "D6010"
        }
        for r in open_items.itertuples():
            pid = r.patient_id
            if pid in busy_patients or pid not in info.index or r.plan_id not in chance:
                continue
            if r.code in COSMETIC and not info.loc[pid].get("marketing_consent", False):
                continue
            needs_first = r.code in ("D2950", "D2740", "D6057", "D6065") and (pid, r.tooth) in blocked
            if needs_first or r.code not in self.catalog:
                continue
            s = float(chance[r.plan_id])
            out.append(
                {
                    "patient_id": pid, "plan_id": r.plan_id, "code": r.code, "column": self._column(r.code),
                    "minutes": self._minutes(r.code), "priority": r.priority,
                    "p_book": s + (1 - s) * lift["treatment"],
                    "reason": ("urgent " if r.priority == "urgent" else "") + "diagnosed, not booked"
                    + ("; said yes already" if r.status == "accepted" else ""),
                }
            )  # fmt: skip
        prior = cfg["office_miss_rate_prior"]
        for c in out:
            c["p_show"] = 1 - miss_rate.get(c["patient_id"], prior)
            c["payer"] = info.loc[c["patient_id"], "payer"]
            c["pref"] = info.loc[c["patient_id"], "preferred_time"] or "any"
        return out

    # assignment

    def _value(self, block: Row, cand: Row) -> tuple[float, float]:
        """(ranking value, expected dollars) of offering this block to this candidate; zeros if it does not fit."""
        pid = block["provider_id"]
        if cand["column"] != block["column"] or pid not in self.eligible.get(cand["code"], set()):
            return 0.0, 0.0
        if self.cal.duration(pid, cand["minutes"]) > block["minutes"]:
            return 0.0, 0.0
        margin = self._margin(cand["code"], cand["payer"], pid)
        if margin <= 0:
            return 0.0, 0.0
        ev = cand["p_book"] * cand["p_show"] * margin
        lo, hi = WINDOWS.get(cand["pref"], WINDOWS["any"])
        fits = lo <= block["start"] < hi and (cand["pref"] != "saturday" or block["date"].weekday() == 5)
        factor = 1.0 if fits else self.cfg["off_preference_factor"]
        return ev * factor * self.cfg["priority_weight"].get(cand["priority"], 1.0), ev * factor

    def _pool(self, blocks: list[Row], cands: list[Row]) -> dict[str, list[Row]]:
        """Each patient's items that fit at least one open stretch, for the strongest patients only."""
        longest: dict[str, int] = {}
        for b in blocks:
            longest[b["provider_id"]] = max(longest.get(b["provider_id"], 0), b["minutes"])
        by_patient: dict[str, list[Row]] = {}
        for c in cands:
            ok = [p for p in self.eligible.get(c["code"], set()) if p in longest]
            if any(self.cal.duration(p, c["minutes"]) <= longest[p] for p in ok):
                by_patient.setdefault(c["patient_id"], []).append(c)
        weight = self.cfg["priority_weight"]

        def strength(items: list[Row]) -> float:
            return max(c["p_book"] * c["p_show"] * self.fallback.get(c["code"], 0) * weight.get(c["priority"], 1.0)
                       for c in items)  # fmt: skip

        ranked = sorted(by_patient, key=lambda p: -strength(by_patient[p]))[: self.cfg["candidates_per_column"]]
        return {p: by_patient[p] for p in ranked}

    def assign(self, blocks: list[Row], cands: list[Row]) -> list[Fill]:
        """Fill open time with the best candidates, nobody used twice.

        Each round offers every open stretch its best remaining patient (an assignment solver, so no patient goes
        to two stretches); a filled stretch keeps its leftover minutes for the next round. Rounds repeat until
        nothing more fits. Specialist and doctor time is planned before hygiene.
        """
        used: set[str] = set()
        fills: list[Fill] = []
        turnover = self.cal.turnover
        for column in ("specialist", "doctor", "hygiene"):
            open_ = [dict(b) for b in blocks if b["column"] == column]
            pool = self._pool(open_, [c for c in cands if c["column"] == column and c["patient_id"] not in used])
            for _ in range(self.cfg["max_rounds"]):
                patients = [p for p in pool if p not in used]
                if not open_ or not patients:
                    break
                rank = np.zeros((len(open_), len(patients)))
                dollars = np.zeros_like(rank)
                pick: dict[tuple[int, int], Row] = {}
                for i, b in enumerate(open_):
                    for j, p in enumerate(patients):
                        for c in pool[p]:
                            r, ev = self._value(b, c)
                            if r > rank[i, j]:
                                rank[i, j], dollars[i, j], pick[(i, j)] = r, ev, c
                rows, cols = linear_sum_assignment(rank, maximize=True)
                placed = 0
                for i, j in zip(rows, cols, strict=True):
                    if rank[i, j] <= 0:
                        continue
                    b, c = open_[i], pick[(i, j)]
                    length = self.cal.duration(b["provider_id"], c["minutes"])
                    used.add(c["patient_id"])
                    placed += 1
                    fills.append(
                        Fill(
                            b["date"].isoformat(), b["provider_id"], column, hhmm(b["start"]), length,
                            c["patient_id"], c["plan_id"], c["code"], round(c["p_book"], 4), round(c["p_show"], 4),
                            round(self._margin(c["code"], c["payer"], b["provider_id"]), 2),
                            round(dollars[i, j], 2), c["reason"],
                        )
                    )  # fmt: skip
                    b["start"] += length + turnover
                    b["minutes"] -= length + turnover
                if not placed:
                    break
                open_ = [b for b in open_ if b["minutes"] >= self.cfg["min_block_minutes"]]
        return sorted(fills, key=lambda f: (f.date, f.start, f.provider_id))

    def at_risk(self, booked: pd.DataFrame, missed: dict[str, float], cands: list[Row], used: set[str]) -> list[AtRisk]:
        """Booked visits likely to be missed: confirm them, and keep a different backup patient ready for each."""
        risky = []
        for r in booked.itertuples():
            p = missed.get(r.appt_id)
            if p is None or p < self.cfg["at_risk_threshold"]:
                continue
            payer = r.payer or ""
            value = sum(self._margin(c, payer, r.provider_id) for c in (r.codes or "").split() if c in self.catalog)
            risky.append((p * value, p, r))
        taken = set(used)
        out = []
        for value_at_risk, p, r in sorted(risky, key=lambda x: -x[0]):
            slot = {"date": r.date, "provider_id": r.provider_id, "column": r.column, "start": to_min(r.start),
                    "minutes": to_min(r.end) - to_min(r.start)}  # fmt: skip
            options = [(self._value(slot, c)[0], c) for c in cands if c["patient_id"] not in taken]
            options = [x for x in options if x[0] > 0]
            backup = max(options, key=lambda x: x[0])[1] if options else None
            if backup:
                taken.add(backup["patient_id"])
            out.append(
                AtRisk(
                    r.appt_id, r.patient_id, r.date.isoformat(), r.start, r.provider_id, round(p, 4),
                    round(value_at_risk, 2), "confirm by phone; keep the backup ready",
                    backup["patient_id"] if backup else "", backup["code"] if backup else "",
                )
            )  # fmt: skip
        return out


def as_rows(items: list) -> list[Row]:
    return [asdict(x) for x in items]
