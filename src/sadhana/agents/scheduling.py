"""Booking from a text thread: what is free, the next openings for a patient, booking and cancelling.

The calendar is rebuilt from the PMS (synced appointments) plus the agents' own bookings and cancellations that
are still waiting to be written back. A slot is re-checked right before it is booked (decision 0007).
"""

from __future__ import annotations

import uuid
from datetime import date, time, timedelta
from typing import Any

import pandas as pd
from sqlalchemy import Connection, insert, text

from sadhana.config import settings
from sadhana.data.schema import pending_bookings
from sadhana.office_calendar import age_at, hhmm, to_min
from sadhana.optimizer.plan import Planner
from sadhana.optimizer.run import gather

Row = dict[str, Any]
CLEANINGS = ("D1110", "D1120", "D4910")


def _hhmm(t: Any) -> str:
    return t.strftime("%H:%M") if isinstance(t, time) else str(t)[:5]


class Scheduler:
    def __init__(self, conn: Connection, office: Row, today: date, horizon_days: int = 120) -> None:
        self.conn, self.office, self.today = conn, office, today
        self.planner = Planner(office, settings.configs_dir, today)
        self.cal = self.planner.cal
        self.g = gather(conn, today, today + timedelta(days=horizon_days))
        self.patients = self.g["patients"].set_index("patient_id")
        pending = conn.execute(
            text("SELECT kind, patient_id, appt_id, date, start, minutes, provider_id FROM pending_bookings")
        ).all()
        self.cancelled = {r.appt_id for r in pending if r.kind == "cancel"}
        self.appts: dict[str, Row] = {}
        for r in self.g["booked"].to_dict("records"):
            self.appts[r["appt_id"]] = r
            if r["appt_id"] not in self.cancelled:
                self.cal.book(r["date"], r["provider_id"], to_min(r["start"]), to_min(r["end"]) - to_min(r["start"]))
        self.busy = set(self.g["future"])
        for r in pending:
            if r.kind == "book" and r.date > today:
                self.cal.book(r.date, r.provider_id, to_min(_hhmm(r.start)), r.minutes)
                self.busy.add(r.patient_id)
        self._cands: list[Row] | None = None

    # what is free

    def free(self, d: date, provider_id: str, start: int, minutes: int, column: str) -> bool:
        hrs = self.cal.hours(d)
        if hrs is None or provider_id not in self.cal.providers(d, column):
            return False
        if start < hrs[0] or start + minutes > hrs[1]:
            return False
        end = start + minutes + self.cal.turnover
        return all(not (start < b_end and b_start < end) for b_start, b_end in self.cal._busy(d, provider_id))

    def visit(self, patient_id: str, code: str) -> Row:
        """Code, length, column and allowed providers for a visit; a cleaning when there is no treatment code."""
        if not code or code in CLEANINGS:
            p = self.patients.loc[patient_id]
            birth = p["birth_date"].date() if pd.notna(p["birth_date"]) else None
            child = birth is not None and age_at(birth, self.today) < 14
            code = "D4910" if p["perio_maintenance"] else ("D1120" if child else "D1110")
            return {"code": code, "minutes": 45 if code == "D1120" else 60, "column": "hygiene",
                    "allowed": self.planner.eligible.get(code, set())}  # fmt: skip
        return {"code": code, "minutes": self.planner._minutes(code), "column": self.planner._column(code),
                "allowed": self.planner.eligible.get(code, set())}  # fmt: skip

    def openings(self, patient_id: str, visit: Row, n: int, earliest: date) -> list[Row]:
        """The patient's next `n` openings for this visit, preferred time first."""
        pref = self.patients.loc[patient_id, "preferred_time"] if patient_id in self.patients.index else "any"
        out, held = [], []
        for _ in range(n * 4):  # different times, not the same time with another provider
            if len(out) >= n:
                break
            hit = self.cal.find(earliest, visit["minutes"], pref or "any", visit["column"], visit["allowed"],
                                horizon=60)  # fmt: skip
            if hit is None:
                break
            d, pid, start, length = hit
            self.cal.book(d, pid, start, length)
            held.append((d, pid, start, length))
            if any(o["date"] == d.isoformat() and o["start"] == hhmm(start) for o in out):
                continue
            out.append({"date": d.isoformat(), "start": hhmm(start), "minutes": length, "provider_id": pid,
                        "column": visit["column"], "code": visit["code"]})  # fmt: skip
        for d, pid, start, length in held:
            self.cal.free(d, pid, start, length)
        return out

    # changes, waiting to be written to the PMS

    def book(self, patient_id: str, opt: Row, source: str, day: date, plan_id: str = "") -> str:
        d = date.fromisoformat(opt["date"]) if isinstance(opt["date"], str) else opt["date"]
        booking_id = f"b-{uuid.uuid4().hex[:16]}"
        self.conn.execute(
            insert(pending_bookings).values(
                office_id=self.office["office_id"], booking_id=booking_id, kind="book", patient_id=patient_id,
                date=d, start=time.fromisoformat(_hhmm(opt["start"])), minutes=opt["minutes"],
                provider_id=opt["provider_id"], column=opt["column"], code=opt.get("code"), plan_id=plan_id or None,
                source=source, created_on=day, status="pending",
            )
        )  # fmt: skip
        self.cal.book(d, opt["provider_id"], to_min(_hhmm(opt["start"])), opt["minutes"])
        self.busy.add(patient_id)
        return booking_id

    def cancel(self, appt_id: str, day: date, source: str, status: str = "pending") -> Row | None:
        """Cancel a booked visit; returns the freed slot. `status` is "in_pms" when the PMS reported it."""
        a = self.appts.get(appt_id)
        if a is None or appt_id in self.cancelled:
            return None
        self.cancelled.add(appt_id)
        start, minutes = to_min(a["start"]), to_min(a["end"]) - to_min(a["start"])
        self.conn.execute(
            insert(pending_bookings).values(
                office_id=self.office["office_id"], booking_id=f"c-{appt_id}", kind="cancel",
                patient_id=a["patient_id"], appt_id=appt_id, date=a["date"], start=time.fromisoformat(a["start"]),
                minutes=minutes, provider_id=a["provider_id"], column=a["column"], source=source, created_on=day,
                status=status,
            )
        )  # fmt: skip
        self.cal.free(a["date"], a["provider_id"], start, minutes)
        self.busy.discard(a["patient_id"])
        codes = a["codes"].split() if isinstance(a["codes"], str) else []
        return {"date": a["date"], "start": a["start"], "minutes": minutes, "provider_id": a["provider_id"],
                "column": a["column"], "code": codes[0] if codes else "", "patient_id": a["patient_id"],
                "appt_id": appt_id}  # fmt: skip

    def candidates(self) -> list[Row]:
        """Everyone worth offering a freed slot to: the planner's candidates (overdue cleanings, open treatment)."""
        if self._cands is None:
            g = self.g
            self._cands = self.planner.candidates(g["patients"], g["recall"], g["plan"], g["accept"], g["miss"],
                                                  g["future"])  # fmt: skip
        return [c for c in self._cands if c["patient_id"] not in self.busy]
