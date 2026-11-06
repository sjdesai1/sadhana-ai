"""The office calendar: dates, holidays, weather, and who is free when.

Shared by the hygiene history (history.py) and the treatment history (treatment.py).
"""

from __future__ import annotations

import random
from bisect import insort
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

Row = dict[str, Any]
DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
WINDOWS = {
    "early_morning": (0, 600),
    "midday": (600, 900),
    "evening": (960, 1440),
    "after_school": (900, 1440),
    "saturday": (0, 1440),
    "any": (0, 1440),
}
DOCTOR_ROLES = {"owner_dentist", "associate_dentist"}


@dataclass
class Appointment:
    appt_id: str
    patient_id: str
    provider_id: str
    column: str  # hygiene, doctor, specialist
    date: str
    start: str
    end: str
    kind: str  # new_patient, recall_adult, recall_child, perio_maintenance, srp, treatment, followup, emergency
    codes: str
    booked_on: str
    status: str  # scheduled, completed, no_show, late_cancel, cancelled
    status_on: str
    rebook_of: str


@dataclass
class AppointmentTruth:
    appt_id: str
    cause: str  # why it did not happen: patient, weather, holiday_week, summer_friday, office_closed, left_practice


@dataclass
class Procedure:
    appt_id: str
    patient_id: str
    date: str
    provider_id: str
    code: str
    tooth: str
    plan_id: str  # the treatment plan item it completes; empty for cleanings, exams and x-rays


def to_min(hhmm: str) -> int:
    h, m = hhmm.split(":")
    return int(h) * 60 + int(m)


def hhmm(m: int) -> str:
    return f"{m // 60:02d}:{m % 60:02d}"


def add_months(d: date, months: int) -> date:
    y, m = divmod(d.month - 1 + months, 12)
    return date(d.year + y, m + 1, min(d.day, 28))


def age_at(birth: date, d: date) -> int:
    return d.year - birth.year - ((d.month, d.day) < (birth.month, birth.day))


def nth_weekday(year: int, month: int, weekday: int, n: int) -> date:
    if n > 0:
        first = date(year, month, 1)
        return first + timedelta(days=(weekday - first.weekday()) % 7 + 7 * (n - 1))
    last = date(year + (month == 12), month % 12 + 1, 1) - timedelta(days=1)
    return last - timedelta(days=(last.weekday() - weekday) % 7)


def holidays(years: range) -> set[date]:
    """Days a typical NYC dental office is closed."""
    out: set[date] = set()
    for y in years:
        thanksgiving = nth_weekday(y, 11, 3, 4)
        out |= {
            date(y, 1, 1),
            nth_weekday(y, 5, 0, -1),  # Memorial Day
            date(y, 7, 4),
            nth_weekday(y, 9, 0, 1),  # Labor Day
            thanksgiving,
            thanksgiving + timedelta(days=1),
            date(y, 12, 24),
            date(y, 12, 25),
        }
    return out


def holiday_week(d: date) -> bool:
    thanksgiving = nth_weekday(d.year, 11, 3, 4)
    return (
        (d.month == 12 and d.day >= 20)
        or (d.month == 1 and d.day <= 2)
        or (d.month == 7 and d.day <= 7)
        or abs((d - thanksgiving).days) <= 3
    )


def weather(rng: random.Random, start: date, end: date, season: Row) -> tuple[set[date], set[date]]:
    """Bad-weather days (more no-shows) and storm days when the office closes."""
    bad: set[date] = set()
    closed: set[date] = set()
    n_closed = season["office_closed_storms_per_winter"]
    for y in range(start.year - 1, end.year + 1):
        winter = [date(y, 12, 1) + timedelta(days=i) for i in range(120)]
        summer = [date(y, 6, 1) + timedelta(days=i) for i in range(92)]
        picks = rng.sample(winter, season["bad_weather_days_per_winter"] + n_closed)
        closed |= set(picks[:n_closed])
        bad |= set(picks[n_closed:])
        bad |= set(rng.sample(summer, season["storm_days_per_summer"]))
    return bad, closed


def works_on(staff: Row, d: date) -> bool:
    """Weekly staff work their listed days; visiting specialists come on set weeks of the month."""
    if DAYS[d.weekday()] not in staff["days"]:
        return False
    week = (d.day - 1) // 7
    freq = staff.get("frequency", "")
    if freq.startswith("2"):
        return week in (0, 2)
    if freq.startswith("1"):
        return week == 1
    return True


class Calendar:
    """Hygiene, doctor and specialist columns: who works which day and which minutes are booked."""

    def __init__(self, office: Row, closed: set[date]) -> None:
        self.office = office
        self.closed = closed
        staff = office["staff"]
        self.columns: dict[str, list[Row]] = {
            "hygiene": [s for s in staff if s["role"] == "hygienist"],
            "doctor": [s for s in staff if s["role"] in DOCTOR_ROLES],
            "specialist": [s for s in staff if s["role"] == "specialist"],
        }
        self.speed = {s["id"]: float(s.get("speed", 1.0)) for s in staff}
        lunch = office.get("lunch")
        self.lunch = (to_min(lunch[0]), to_min(lunch[1])) if lunch else None
        self.turnover = int(office.get("turnover_min", 10))
        self.booked: dict[tuple[date, str], list[tuple[int, int]]] = {}
        self.free_min: dict[tuple[date, str], int] = {}
        self._day: dict[date, tuple[tuple[int, int] | None, dict[str, list[str]]]] = {}

    def _info(self, d: date) -> tuple[tuple[int, int] | None, dict[str, list[str]]]:
        if d not in self._day:
            h = self.office["hours"].get(DAYS[d.weekday()])
            hrs = None if d in self.closed or not h else (to_min(h[0]), to_min(h[1]))
            cols = {c: [s["id"] for s in staff if works_on(s, d)] if hrs else [] for c, staff in self.columns.items()}
            self._day[d] = (hrs, cols)
        return self._day[d]

    def hours(self, d: date) -> tuple[int, int] | None:
        return self._info(d)[0]

    def providers(self, d: date, column: str = "hygiene") -> list[str]:
        return self._info(d)[1][column]

    def day_minutes(self, d: date) -> int:
        hrs = self.hours(d)
        if not hrs:
            return 0
        return hrs[1] - hrs[0] - (self.lunch[1] - self.lunch[0] if self.lunch else 0)

    def capacity(self, d: date, column: str = "hygiene") -> int:
        return self.day_minutes(d) * len(self.providers(d, column))

    def duration(self, pid: str, minutes: int) -> int:
        """Slower providers (a junior dentist) take longer; rounded up to 10-minute blocks."""
        m = round(minutes * self.speed.get(pid, 1.0))
        return m + (-m % 10)

    def _busy(self, d: date, pid: str) -> list[tuple[int, int]]:
        key = (d, pid)
        if key not in self.booked:
            self.booked[key] = [self.lunch] if self.lunch else []
            self.free_min[key] = self.day_minutes(d)
        return self.booked[key]

    def _fit(
        self, d: date, pid: str, minutes: int, window: tuple[int, int], extra: list[tuple[int, int]] | None
    ) -> int | None:
        hrs = self.hours(d)
        if hrs is None:
            return None
        busy = self._busy(d, pid)
        if self.free_min[(d, pid)] < minutes:
            return None
        if extra:
            busy = sorted(busy + extra)
        open_, close = hrs
        t = max(open_, window[0])
        t += -t % 10
        for b_start, b_end in busy:
            if t + minutes + self.turnover <= b_start:
                break
            t = max(t, b_end)
        if t + minutes <= close and t < window[1]:
            return t
        return None

    def find(
        self,
        earliest: date,
        minutes: int,
        pref: str,
        column: str = "hygiene",
        allowed: set[str] | None = None,
        patient_busy: dict[date, list[tuple[int, int]]] | None = None,
        horizon: int = 180,
    ) -> tuple[date, str, int, int] | None:
        """First opening on or after `earliest`: (day, provider, start minute, length). Tries the patient's
        preferred time for five weeks first, then any time."""
        passes = [True, False] if pref != "any" and horizon > 35 else [False]
        for strict in passes:
            for i in range(35 if strict else horizon):
                d = earliest + timedelta(days=i)
                hrs, cols = self._info(d)
                if hrs is None:
                    continue
                window = WINDOWS["any"]
                if strict:
                    if pref == "saturday" and d.weekday() != 5:
                        continue
                    if not (pref == "after_school" and d.weekday() == 5):
                        window = WINDOWS.get(pref, WINDOWS["any"])
                    if window[0] >= hrs[1] - minutes:
                        continue
                provs = [p for p in cols[column] if allowed is None or p in allowed]
                extra = patient_busy.get(d) if patient_busy else None
                for pid in sorted(provs, key=lambda x: (-self.free_min.get((d, x), 10_000), x)):
                    length = self.duration(pid, minutes)
                    t = self._fit(d, pid, length, window, extra)
                    if t is not None:
                        return d, pid, t, length
        return None

    def book(self, d: date, pid: str, start: int, minutes: int) -> None:
        insort(self._busy(d, pid), (start, start + minutes + self.turnover))
        self.free_min[(d, pid)] -= minutes + self.turnover

    def free(self, d: date, pid: str, start: int, minutes: int) -> None:
        self.booked[(d, pid)].remove((start, start + minutes + self.turnover))
        self.free_min[(d, pid)] += minutes + self.turnover
