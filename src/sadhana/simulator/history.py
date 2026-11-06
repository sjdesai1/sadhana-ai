"""Simulator part 3a: the clock.

Plays out three years of the office's hygiene schedule one day at a time, ending the day before SIM_TODAY:
- every patient has a join date; some joined during the window (new patients), some left (former patients)
- visits are booked into real provider time slots: office hours, lunch, turnover, holidays, preferred times
- each visit is kept, a no-show, a late cancel (inside 24 hours) or an early cancel that frees the slot
- seasonality: holiday weeks, summer Fridays, bad-weather days and storm closures, December slowdown,
  summer vacations, back-to-school for kids, year-end rush to use insurance benefits
- what the office already does today: basic reminder texts, booking the next cleaning at checkout and
  calling no-shows to rebook. Sadhana is measured against this baseline, not against nothing.

Observed: appointments.csv, patient_history.csv. Hidden: appointment_truth.csv, flow_truth.csv.
Treatment visits, specialists and emergencies come in part 3b.
"""

from __future__ import annotations

import csv
import heapq
import json
import random
from bisect import insort
from dataclasses import asdict, dataclass, replace
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from sadhana.simulator.charts import Perio
from sadhana.simulator.population import SIM_TODAY, Patient, Truth, generate

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
NO_ANNUAL_MAX = {"Cash", "NY Medicaid"}
# event order inside one day: people leave, early cancels free slots, visits happen, then new bookings
LEAVE, CANCEL, VISIT, REQUEST = 0, 1, 2, 3


@dataclass
class Appointment:
    appt_id: str
    patient_id: str
    provider_id: str
    column: str  # hygiene (doctor and specialist columns come in part 3b)
    date: str
    start: str
    end: str
    kind: str  # new_patient, recall_adult, recall_child, perio_maintenance
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
class PatientHistory:
    patient_id: str
    first_visit: str
    last_visit: str
    next_appointment: str
    completed_visits: int
    no_shows: int
    late_cancels: int
    status: str  # active, inactive, archived (the office knows they left)


@dataclass
class FlowTruth:
    patient_id: str
    joined_on: str
    left_on: str
    left_reason: str  # moved, switched_dentist, insurance_change, unhappy, deceased, ""
    office_knows: bool
    lapsed_on: str  # stopped booking without telling anyone


@dataclass
class _State:
    patient: Patient
    truth: Truth
    reminded: bool  # the office's reminder texts or emails reach this patient (or the parent)
    maintenance: bool
    joined_on: date
    left_on: date | None = None
    left_reason: str = ""
    office_knows: bool = False
    lapsed_on: date | None = None
    first_visit: date | None = None
    last_visit: date | None = None
    last_bitewings: date | None = None
    last_full_xrays: date | None = None
    future_appt: str = ""
    completed: int = 0
    no_shows: int = 0
    late_cancels: int = 0
    left: bool = False


def load_clock(configs_dir: Path) -> Row:
    return json.loads((configs_dir / "simulator" / "clock.json").read_text())


def _min(hhmm: str) -> int:
    h, m = hhmm.split(":")
    return int(h) * 60 + int(m)


def _hhmm(m: int) -> str:
    return f"{m // 60:02d}:{m % 60:02d}"


def _add_months(d: date, months: int) -> date:
    y, m = divmod(d.month - 1 + months, 12)
    return date(d.year + y, m + 1, min(d.day, 28))


def _age_at(birth: date, d: date) -> int:
    return d.year - birth.year - ((d.month, d.day) < (birth.month, birth.day))


def _nth_weekday(year: int, month: int, weekday: int, n: int) -> date:
    if n > 0:
        first = date(year, month, 1)
        return first + timedelta(days=(weekday - first.weekday()) % 7 + 7 * (n - 1))
    last = date(year + (month == 12), month % 12 + 1, 1) - timedelta(days=1)
    return last - timedelta(days=(last.weekday() - weekday) % 7)


def holidays(years: range) -> set[date]:
    """Days a typical NYC dental office is closed."""
    out: set[date] = set()
    for y in years:
        thanksgiving = _nth_weekday(y, 11, 3, 4)
        out |= {
            date(y, 1, 1),
            _nth_weekday(y, 5, 0, -1),  # Memorial Day
            date(y, 7, 4),
            _nth_weekday(y, 9, 0, 1),  # Labor Day
            thanksgiving,
            thanksgiving + timedelta(days=1),
            date(y, 12, 24),
            date(y, 12, 25),
        }
    return out


def holiday_week(d: date) -> bool:
    thanksgiving = _nth_weekday(d.year, 11, 3, 4)
    return (
        (d.month == 12 and d.day >= 20)
        or (d.month == 1 and d.day <= 2)
        or (d.month == 7 and d.day <= 7)
        or abs((d - thanksgiving).days) <= 3
    )


def _weather(rng: random.Random, start: date, end: date, season: Row) -> tuple[set[date], set[date]]:
    """Bad-weather days (more no-shows) and storm days when the office closes."""
    bad: set[date] = set()
    closed: set[date] = set()
    for y in range(start.year - 1, end.year + 1):
        winter = [date(y, 12, 1) + timedelta(days=i) for i in range(120)]
        summer = [date(y, 6, 1) + timedelta(days=i) for i in range(92)]
        picks = rng.sample(winter, season["bad_weather_days_per_winter"] + season["office_closed_storms_per_winter"])
        closed |= set(picks[: season["office_closed_storms_per_winter"]])
        bad |= set(picks[season["office_closed_storms_per_winter"] :])
        bad |= set(rng.sample(summer, season["storm_days_per_summer"]))
    return bad, closed


class Calendar:
    """Hygiene columns: who works which day, and which minutes are already booked."""

    def __init__(self, office: Row, closed: set[date]) -> None:
        self.office = office
        self.closed = closed
        self.hygienists = [s for s in office["staff"] if s["role"] == "hygienist"]
        lunch = office.get("lunch")
        self.lunch = (_min(lunch[0]), _min(lunch[1])) if lunch else None
        self.turnover = int(office.get("turnover_min", 10))
        self.booked: dict[tuple[date, str], list[tuple[int, int]]] = {}
        self.free_min: dict[tuple[date, str], int] = {}
        self._day: dict[date, tuple[tuple[int, int] | None, list[str]]] = {}

    def _info(self, d: date) -> tuple[tuple[int, int] | None, list[str]]:
        if d not in self._day:
            h = self.office["hours"].get(DAYS[d.weekday()])
            hrs = None if d in self.closed or not h else (_min(h[0]), _min(h[1]))
            provs = [s["id"] for s in self.hygienists if DAYS[d.weekday()] in s["days"]] if hrs else []
            self._day[d] = (hrs, provs)
        return self._day[d]

    def hours(self, d: date) -> tuple[int, int] | None:
        return self._info(d)[0]

    def providers(self, d: date) -> list[str]:
        return self._info(d)[1]

    def capacity(self, d: date) -> int:
        hrs, provs = self._info(d)
        if not hrs:
            return 0
        lunch = self.lunch[1] - self.lunch[0] if self.lunch else 0
        return (hrs[1] - hrs[0] - lunch) * len(provs)

    def _busy(self, d: date, pid: str) -> list[tuple[int, int]]:
        key = (d, pid)
        if key not in self.booked:
            self.booked[key] = [self.lunch] if self.lunch else []
            self.free_min[key] = self.capacity(d) // max(1, len(self.providers(d)))
        return self.booked[key]

    def _fit(self, d: date, pid: str, minutes: int, window: tuple[int, int]) -> int | None:
        hrs = self.hours(d)
        if hrs is None:
            return None
        busy = self._busy(d, pid)
        if self.free_min[(d, pid)] < minutes:
            return None
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

    def find(self, earliest: date, minutes: int, pref: str) -> tuple[date, str, int] | None:
        passes = [True, False] if pref != "any" else [False]
        for strict in passes:
            for i in range(35 if strict else 180):
                d = earliest + timedelta(days=i)
                hrs, provs = self._info(d)
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
                for pid in sorted(provs, key=lambda x: (-self.free_min.get((d, x), 10_000), x)):
                    t = self._fit(d, pid, minutes, window)
                    if t is not None:
                        return d, pid, t
        return None

    def book(self, d: date, pid: str, start: int, minutes: int) -> None:
        insort(self._busy(d, pid), (start, start + minutes + self.turnover))
        self.free_min[(d, pid)] -= minutes + self.turnover

    def free(self, d: date, pid: str, start: int, minutes: int) -> None:
        self.booked[(d, pid)].remove((start, start + minutes + self.turnover))
        self.free_min[(d, pid)] += minutes + self.turnover


def former_patients(
    office: Row, configs_dir: Path, payers: dict[str, Row], current: list[Patient], seed: int = 42
) -> tuple[list[Patient], list[Truth]]:
    """Patients who were seen during the window and are gone by SIM_TODAY. Same generator, numbered after the
    current patients, so a PMS export holds both."""
    share = load_clock(configs_dir)["patient_flow"]["former_patient_share"]
    sub = {**office, "simulation": {**office["simulation"], "active_patients": round(len(current) * share)}}
    patients, truths = generate(sub, configs_dir, payers, seed=seed + 1_000_000)
    p_off = len(current)
    h_off = len({p.household_id for p in current})
    pid = {p.patient_id: f"P{i + p_off + 1:06d}" for i, p in enumerate(patients)}
    hid = {h: f"H{int(h[1:]) + h_off:05d}" for h in {p.household_id for p in patients}}
    patients = [
        replace(
            p,
            patient_id=pid[p.patient_id],
            household_id=hid[p.household_id],
            guardian_id=pid.get(p.guardian_id, ""),
            subscriber_id=pid.get(p.subscriber_id, ""),
        )
        for p in patients
    ]
    truths = [replace(t, patient_id=pid[t.patient_id]) for t in truths]
    return patients, truths


def _window_days(start: date, end: date, month_w: Row, recency: float) -> tuple[list[date], list[float]]:
    days = [start + timedelta(days=i) for i in range((end - start).days)]
    cum, total = [], 0.0
    for i, d in enumerate(days):
        total += month_w[str(d.month)] * (1 + recency * i / len(days))
        cum.append(total)
    return days, cum


def run(
    office: Row,
    patients: list[Patient],
    truths: list[Truth],
    perio: list[Perio],
    configs_dir: Path,
    former_ids: set[str],
    seed: int = 42,
) -> tuple[list[Appointment], list[AppointmentTruth], list[PatientHistory], list[FlowTruth]]:
    clock = load_clock(configs_dir)
    flow, recall, visits, out_cfg, season = (
        clock[k] for k in ("patient_flow", "recall", "visits", "outcomes", "seasonality")
    )
    rng = random.Random(f"history-{office['office_id']}-{seed}")
    start = date(SIM_TODAY.year - clock["history_years"], SIM_TODAY.month, SIM_TODAY.day)
    last_bookable = SIM_TODAY + timedelta(days=clock["future_booking_days"])
    bad_days, storm_closed = _weather(rng, start, last_bookable, season)
    cal = Calendar(office, holidays(range(start.year, last_bookable.year + 1)))
    maintenance = {x.patient_id: x.maintenance for x in perio}
    by_id = {p.patient_id: p for p in patients}

    def reached(p: Patient) -> bool:
        contact = by_id.get(p.guardian_id, p) if p.age < 18 else p
        return contact.sms_consent or contact.email_on_file

    # 1. join and leave dates, by household
    state: dict[str, _State] = {}
    houses: dict[str, list[tuple[Patient, Truth]]] = {}
    for p, t in zip(patients, truths, strict=True):
        houses.setdefault(p.household_id, []).append((p, t))
    current_days, current_cum = _window_days(start, SIM_TODAY, flow["new_patient_month_weights"], 0.5)
    former_days, former_cum = _window_days(
        start, SIM_TODAY - timedelta(days=150), flow["new_patient_month_weights"], 0.0
    )
    reasons = flow["leave_reasons"]
    for members in houses.values():
        former = members[0][0].patient_id in former_ids
        share = flow["former_joined_before_window"] if former else 1 - flow["joined_in_window_share"]
        if rng.random() < share:
            joined = start - timedelta(days=rng.randint(180, 15 * 365))
        elif former:
            joined = rng.choices(former_days, cum_weights=former_cum)[0]
        else:
            joined = rng.choices(current_days, cum_weights=current_cum)[0]
        left_on, reason = None, ""
        if former:
            lo = max(joined, start) + timedelta(days=60)
            left_on = lo + timedelta(days=rng.randint(0, max(0, (SIM_TODAY - lo).days - 14)))
            reason = rng.choices(list(reasons), weights=list(reasons.values()))[0]
        for p, t in members:
            r = "deceased" if former and p.age >= 78 and rng.random() < 0.4 else reason
            state[p.patient_id] = _State(
                p,
                t,
                reached(p),
                maintenance.get(p.patient_id, False),
                joined,
                left_on,
                r,
                bool(r) and rng.random() < flow["office_learns_reason"][r],
            )

    events: list[tuple[date, int, int, str, Row]] = []
    seq = 0

    def push(d: date, prio: int, key: str, payload: Row | None = None) -> None:
        nonlocal seq
        seq += 1
        heapq.heappush(events, (d, prio, seq, key, payload or {}))

    def kind_for(st: _State, d: date, new: bool) -> str:
        if new:
            return "new_patient"
        if st.maintenance and _age_at(date.fromisoformat(st.patient.birth_date), d) >= 18:
            return "perio_maintenance"
        child = _age_at(date.fromisoformat(st.patient.birth_date), d) < visits["child_cleaning_under"]
        return "recall_child" if child else "recall_adult"

    def codes_for(st: _State, kind: str, d: date) -> str:
        if kind == "new_patient":
            child = _age_at(date.fromisoformat(st.patient.birth_date), d) < visits["child_cleaning_under"]
            return "D0150 D0210 " + ("D1120 D1206" if child else "D1110")
        codes = ["D0120", {"recall_adult": "D1110", "recall_child": "D1120", "perio_maintenance": "D4910"}[kind]]
        if not st.last_bitewings or (d - st.last_bitewings).days >= 30 * visits["bitewings_every_months"]:
            codes.append("D0274")
        if not st.last_full_xrays or (d - st.last_full_xrays).days >= 365 * visits["full_xrays_every_years"]:
            codes.append("D0210")
        if kind == "recall_child":
            codes.append("D1206")
        return " ".join(codes)

    def self_book(st: _State, due: date, today: date) -> None:
        """Patient books on their own, late or never; the office's reminder nudges a little."""
        c = st.truth.recall_compliance
        p_lapse = recall["lapse_base"] + recall["lapse_per_noncompliance_sq"] * (1 - c) ** 2
        mean_delay = recall["delay_base_days"] + recall["delay_per_noncompliance_days"] * (1 - c)
        if st.reminded:
            p_lapse *= recall["office_reminder_lapse_factor"]
            mean_delay *= recall["office_reminder_delay_factor"]
        if rng.random() < p_lapse:
            st.lapsed_on = due
            return
        req = due + timedelta(days=int(rng.expovariate(1 / mean_delay)))
        adult = st.patient.age >= 18
        if adult and req.month in (7, 8) and rng.random() < season["summer_postpone_adult"]:
            req += timedelta(days=rng.randint(14, 35))
        if req.month == 12 and rng.random() < season["december_postpone"]:
            req = date(req.year + 1, 1, rng.randint(3, 20))
        if not adult and req.month in (5, 6, 7, 9) and rng.random() < season["back_to_school_pull_child"]:
            req = date(req.year, 8, rng.randint(1, 28))
        insured = st.patient.payer not in NO_ANNUAL_MAX
        if insured and req.month in (1, 2) and rng.random() < season["year_end_benefit_pull"]:
            pulled = date(req.year - 1, 12, rng.randint(1, 18))
            if st.last_visit is None or (pulled - st.last_visit).days >= 150:
                req = pulled
        push(max(req, today + timedelta(days=1)), REQUEST, st.patient.patient_id)

    def next_recall(st: _State, d: date) -> None:
        months = recall["perio_maintenance_months"] if st.maintenance else recall["months"]
        due = _add_months(d, months)
        c = st.truth.recall_compliance
        earliest = due - timedelta(days=rng.randint(0, 10))
        kid = st.patient.age < 18 and _age_at(date.fromisoformat(st.patient.birth_date), due) < 18
        if kid and due.month in (6, 7, 9) and rng.random() < season["back_to_school_pull_child"]:
            earliest = max(date(due.year, 8, rng.randint(1, 20)), d + timedelta(days=120))
        if rng.random() < recall["preappoint_base"] + recall["preappoint_per_compliance"] * c:
            push(d, REQUEST, st.patient.patient_id, {"earliest": earliest})
        else:
            self_book(st, due, d)

    # 2. starting point: established patients are mid-cycle at the start of the window, new patients call in
    first_age = flow["first_visit_age"]
    for pid, st in state.items():
        c = st.truth.recall_compliance
        birth = date.fromisoformat(st.patient.birth_date)
        if st.joined_on >= start or _age_at(birth, start) < first_age:
            push(max(st.joined_on, start), REQUEST, pid, {"new": True})
        elif rng.random() < 0.15 * (1 - c):
            st.lapsed_on = start - timedelta(days=rng.randint(30, 700))
            st.last_visit = st.lapsed_on - timedelta(days=180)
        else:
            cycle = 90 if st.maintenance else 182
            st.last_visit = start - timedelta(days=rng.randint(0, cycle))
            st.last_bitewings = st.last_visit - timedelta(days=rng.choice([0, 182]))
            st.last_full_xrays = st.last_visit - timedelta(days=rng.randint(0, 5 * 365))
            months = recall["perio_maintenance_months"] if st.maintenance else recall["months"]
            due = _add_months(st.last_visit, months)
            if rng.random() < recall["preappoint_base"] + recall["preappoint_per_compliance"] * c:
                push(start, REQUEST, pid, {"earliest": max(due, start)})
            else:
                self_book(st, due, start - timedelta(days=1))
        if st.left_on:
            push(st.left_on, LEAVE, pid)

    appts: dict[str, Appointment] = {}
    causes: dict[str, str] = {}
    minutes_of: dict[str, int] = {}

    def cancel(a: Appointment, d: date, status: str, cause: str) -> None:
        a.status, a.status_on = status, d.isoformat()
        causes[a.appt_id] = cause
        state[a.patient_id].future_appt = ""
        if status == "cancelled":
            cal.free(date.fromisoformat(a.date), a.provider_id, _min(a.start), minutes_of[a.appt_id])

    def request(d: date, pid: str, payload: Row) -> None:
        st = state[pid]
        if st.left or st.future_appt or (st.lapsed_on and not payload):
            return
        birth = date.fromisoformat(st.patient.birth_date)
        ready = date(birth.year + first_age, birth.month, min(birth.day, 28))
        if d < ready:
            push(ready + timedelta(days=rng.randint(0, 120)), REQUEST, pid, payload)
            return
        new = st.last_visit is None
        earliest = payload.get("earliest") or d + timedelta(days=rng.randint(2, 10) if new else rng.randint(1, 14))
        earliest = max(earliest, d + timedelta(days=1))
        if new and earliest.month == 12 and earliest.day >= 15 and rng.random() < season["december_postpone"]:
            earliest = date(earliest.year + 1, 1, rng.randint(3, 15))  # "let's do it after the holidays"
        kind = kind_for(st, earliest, new)
        minutes = visits["minutes"][kind]
        pref = st.patient.preferred_time
        if pref == "after_school" and earliest.month in (7, 8):
            pref = "any"  # school is out
        slot = cal.find(earliest, minutes, pref)
        if slot is None or slot[0] > last_bookable:
            if d + timedelta(days=30) < SIM_TODAY:
                push(d + timedelta(days=30), REQUEST, pid, payload)
            return
        day, prov, t = slot
        wait = (day - earliest).days
        if (
            "earliest" not in payload
            and wait > recall["booked_out_days"]
            and rng.random() < recall["booked_out_decline"]
        ):
            # the first opening is weeks away: "call me if something opens up", and some never call back
            if rng.random() < recall["booked_out_drift"]:
                st.lapsed_on = d
            elif d + timedelta(days=45) < SIM_TODAY:
                push(d + timedelta(days=rng.randint(30, 75)), REQUEST, pid, payload)
            return
        cal.book(day, prov, t, minutes)
        aid = f"A{len(appts) + 1:07d}"
        a = Appointment(
            aid,
            pid,
            prov,
            "hygiene",
            day.isoformat(),
            _hhmm(t),
            _hhmm(t + minutes),
            kind,
            codes_for(st, kind, day),
            d.isoformat(),
            "scheduled",
            "",
            payload.get("rebook_of", ""),
        )
        appts[aid], minutes_of[aid] = a, minutes
        st.future_appt = aid
        lead = (day - d).days
        c = st.truth.recall_compliance
        p_early = out_cfg["early_cancel_base"] + out_cfg["early_cancel_per_noncompliance"] * (1 - c)
        if lead > out_cfg["booked_far_out_days"]:
            p_early += out_cfg["early_cancel_far_out_extra"]
        if lead >= 3 and rng.random() < p_early:
            push(d + timedelta(days=rng.randint(1, lead - 2)), CANCEL, aid)
        push(day, VISIT, aid)

    def visit(d: date, aid: str) -> None:
        a = appts[aid]
        if a.status != "scheduled":
            return
        st = state[a.patient_id]
        if st.left:
            # the office never heard they left: half never answer the confirmation calls and the slot is
            # cancelled the day before, the rest simply do not show up
            if rng.random() < 0.5:
                cancel(a, d - timedelta(days=1), "cancelled", "left_practice")
            else:
                st.no_shows += 1
                cancel(a, d, "no_show", "left_practice")
            return
        if d in storm_closed:
            cancel(a, d, "cancelled", "office_closed")
            push(d + timedelta(days=1), REQUEST, a.patient_id, {"rebook_of": aid})
            return
        t = st.truth
        ns, lc, cause = 1.0, 1.0, "patient"
        if st.reminded:
            ns *= out_cfg["office_reminder_no_show_factor"]
            lc *= out_cfg["office_reminder_late_cancel_factor"]
        if a.kind == "new_patient":
            ns *= out_cfg["new_patient_no_show_factor"]
        if (d - date.fromisoformat(a.booked_on)).days > out_cfg["booked_far_out_days"]:
            ns *= out_cfg["booked_far_out_factor"]
        ns *= season["no_show_day_of_week"].get(DAYS[d.weekday()], 1.0)
        if d.weekday() == 4 and d.month in (6, 7, 8):
            ns *= season["summer_friday_factor"]
            cause = "summer_friday"
        if holiday_week(d):
            ns *= season["holiday_week_factor"]
            cause = "holiday_week"
        if d in bad_days:
            ns *= season["weather_no_show_factor"]
            lc *= season["weather_late_cancel_factor"]
            cause = "weather"
        r = rng.random()
        if r < t.no_show_rate * ns:
            st.no_shows += 1
            cancel(a, d, "no_show", cause)
            c = t.recall_compliance
            if rng.random() < out_cfg["rebook_after_no_show_base"] + out_cfg["rebook_after_no_show_per_compliance"] * c:
                push(d + timedelta(days=rng.randint(2, 21)), REQUEST, a.patient_id, {"rebook_of": aid})
            else:
                self_book(st, d, d)
        elif r < t.no_show_rate * ns + t.late_cancel_rate * lc:
            st.late_cancels += 1
            cancel(a, d - timedelta(days=rng.choice([0, 1])), "late_cancel", cause)
            if rng.random() < out_cfg["reschedule_after_late_cancel"]:
                push(d + timedelta(days=rng.randint(3, 30)), REQUEST, a.patient_id, {"rebook_of": aid})
            else:
                self_book(st, d, d)
        else:
            a.status, a.status_on = "completed", d.isoformat()
            st.future_appt = ""
            st.completed += 1
            st.first_visit = st.first_visit or d
            st.last_visit = d
            if "D0274" in a.codes:
                st.last_bitewings = d
            if "D0210" in a.codes:
                st.last_full_xrays = d
            next_recall(st, d)

    # 3. the clock
    while events and events[0][0] < SIM_TODAY:
        d, prio, _, key, payload = heapq.heappop(events)
        if prio == LEAVE:
            st = state[key]
            st.left = True
            a = appts.get(st.future_appt)
            if a and st.office_knows:
                cancel(a, d, "cancelled", "left_practice")
        elif prio == CANCEL:
            a = appts[key]
            if a.status == "scheduled":
                cancel(a, d, "cancelled", "patient")
                st = state[a.patient_id]
                if rng.random() < out_cfg["reschedule_after_early_cancel"]:
                    push(d, REQUEST, a.patient_id, {"rebook_of": key})
                else:
                    self_book(st, date.fromisoformat(a.date), d)
        elif prio == VISIT:
            visit(d, key)
        else:
            request(d, key, payload)

    # 4. what the office's system shows on SIM_TODAY, and the hidden story behind it
    history, flows = [], []
    inactive_after = SIM_TODAY - timedelta(days=548)  # offices call a patient inactive after 18 months
    for pid, st in state.items():
        nxt = appts.get(st.future_appt)
        if st.left and st.office_knows:
            status = "archived"
        elif nxt or (st.last_visit and st.last_visit >= inactive_after):
            status = "active"
        else:
            status = "inactive"
        first = st.first_visit or (st.joined_on if st.joined_on < start else None)
        history.append(
            PatientHistory(
                pid,
                first.isoformat() if first else "",
                st.last_visit.isoformat() if st.last_visit else "",
                nxt.date if nxt else "",
                st.completed,
                st.no_shows,
                st.late_cancels,
                status,
            )
        )
        flows.append(
            FlowTruth(
                pid,
                st.joined_on.isoformat(),
                st.left_on.isoformat() if st.left_on else "",
                st.left_reason,
                st.office_knows,
                st.lapsed_on.isoformat() if st.lapsed_on else "",
            )
        )
    appointments = sorted(appts.values(), key=lambda a: (a.date, a.start, a.provider_id))
    truth_rows = [AppointmentTruth(a.appt_id, causes.get(a.appt_id, "")) for a in appointments]
    return appointments, truth_rows, history, flows


def overdue_share(history: list[PatientHistory], flows: list[FlowTruth], perio: list[Perio]) -> float:
    """Share of active patients past due for a cleaning with nothing booked: the recall gap Sadhana works."""
    gone = {f.patient_id for f in flows if f.left_on}
    maint = {x.patient_id for x in perio if x.maintenance}
    active = [h for h in history if h.status == "active" and h.patient_id not in gone and h.last_visit]
    overdue = 0
    for h in active:
        months = 3 if h.patient_id in maint else 6
        if not h.next_appointment and date.fromisoformat(h.last_visit) < _add_months(SIM_TODAY, -months - 1):
            overdue += 1
    return overdue / len(active)


def utilization(appointments: list[Appointment], office: Row, months: int = 12) -> float:
    """Booked hygiene minutes kept or held through the day (completed, no-show, late cancel) over capacity."""
    cal = Calendar(office, holidays(range(SIM_TODAY.year - 2, SIM_TODAY.year + 1)))
    lo = _add_months(SIM_TODAY, -months)
    cap = sum(cal.capacity(lo + timedelta(days=i)) for i in range((SIM_TODAY - lo).days))
    used = sum(
        _min(a.end) - _min(a.start) + cal.turnover
        for a in appointments
        if a.status in ("completed", "no_show", "late_cancel") and lo <= date.fromisoformat(a.date) < SIM_TODAY
    )
    return used / cap


def write(
    appointments: list[Appointment],
    appt_truth: list[AppointmentTruth],
    history: list[PatientHistory],
    flows: list[FlowTruth],
    out_dir: Path,
) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    tables: dict[str, list] = {
        "appointments.csv": appointments,
        "appointment_truth.csv": appt_truth,
        "patient_history.csv": history,
        "flow_truth.csv": flows,
    }
    for name, rows in tables.items():
        with (out_dir / name).open("w", newline="") as f:
            dicts = [asdict(r) for r in rows]
            writer = csv.DictWriter(f, fieldnames=list(dicts[0]))
            writer.writeheader()
            writer.writerows(dicts)
    return out_dir
