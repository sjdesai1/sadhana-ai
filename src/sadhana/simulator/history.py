"""Simulator part 3: the clock.

Plays out three years of the office one day at a time, ending the day before SIM_TODAY:
- every patient has a join date; some joined during the window (new patients), some left (former patients)
- visits are booked into real provider time slots: office hours, lunch, turnover, holidays, preferred times,
  provider speed, and never two places at once for the same patient
- each visit is kept, a no-show, a late cancel (inside 24 hours) or an early cancel that frees the slot
- seasonality: holiday weeks, summer Fridays, bad-weather days and storm closures, December slowdown,
  summer vacations, back-to-school for kids, year-end rush to use insurance benefits
- what the office already does today: basic reminder texts, booking the next cleaning at checkout and
  calling no-shows to rebook. Sadhana is measured against this baseline, not against nothing.
- treatment (treatment.py): exams, findings, plan decisions, doctor and specialist visits, emergencies

Observed: appointments.csv, procedures.csv, patient_history.csv, treatment_plan.csv.
Hidden: appointment_truth.csv, flow_truth.csv, conditions.csv.
"""

from __future__ import annotations

import csv
import heapq
import json
import random
from dataclasses import asdict, dataclass, field, replace
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from sadhana.simulator.charts import Condition, Perio, PlanItem, Tooth
from sadhana.simulator.population import SIM_TODAY, Patient, Truth, generate
from sadhana.simulator.schedule import (
    DAYS,
    Appointment,
    AppointmentTruth,
    Calendar,
    Procedure,
    add_months,
    age_at,
    hhmm,
    holiday_week,
    holidays,
    to_min,
    weather,
)
from sadhana.simulator.treatment import TreatmentMixin

__all__ = ["DAYS", "holiday_week", "holidays", "run", "former_patients", "overdue_share", "utilization", "write"]

Row = dict[str, Any]
NO_ANNUAL_MAX = {"Cash", "NY Medicaid"}
HYGIENE_KINDS = {"new_patient", "recall_adult", "recall_child", "perio_maintenance"}
TREATMENT_KINDS = {"srp", "treatment", "followup"}
# event order inside one day: people leave, early cancels free slots, pain starts, visits happen, then bookings
LEAVE, CANCEL, PAIN, VISIT, REQUEST = 0, 1, 2, 3, 4


@dataclass
class PatientHistory:
    patient_id: str
    first_visit: str
    last_visit: str
    last_cleaning: str
    next_appointment: str
    next_cleaning: str
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
    lapsed_on: str  # stopped booking cleanings without telling anyone
    emergencies_turned_away: int


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
    last_any: date | None = None
    last_visit: date | None = None  # last cleaning
    last_exam: date | None = None
    last_bitewings: date | None = None
    last_full_xrays: date | None = None
    future_appt: str = ""  # next cleaning on the books
    busy: dict[date, list[tuple[int, int]]] = field(default_factory=dict)
    presented: bool = False
    completed: int = 0
    no_shows: int = 0
    late_cancels: int = 0
    turned_away: int = 0
    left: bool = False


def load_clock(configs_dir: Path) -> Row:
    return json.loads((configs_dir / "simulator" / "clock.json").read_text())


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


class Practice(TreatmentMixin):
    """One office, played forward day by day."""

    def __init__(
        self,
        office: Row,
        patients: list[Patient],
        truths: list[Truth],
        teeth: list[Tooth],
        conds: list[Condition],
        perio: list[Perio],
        plan: list[PlanItem],
        configs_dir: Path,
        former_ids: set[str],
        seed: int,
    ) -> None:
        clock = load_clock(configs_dir)
        self.office = office
        self.flow, self.recall, self.visits, self.out_cfg, self.season, self.tx = (
            clock[k] for k in ("patient_flow", "recall", "visits", "outcomes", "seasonality", "treatment")
        )
        self.rng = random.Random(f"history-{office['office_id']}-{seed}")
        self.start = date(SIM_TODAY.year - clock["history_years"], SIM_TODAY.month, SIM_TODAY.day)
        self.last_bookable = SIM_TODAY + timedelta(days=clock["future_booking_days"])
        self.bad_days, self.storm_closed = weather(self.rng, self.start, self.last_bookable, self.season)
        self.cal = Calendar(office, holidays(range(self.start.year, self.last_bookable.year + 1)))
        self.events: list[tuple[date, int, int, str, Row]] = []
        self.seq = 0
        self.appts: dict[str, Appointment] = {}
        self.causes: dict[str, str] = {}
        self.payload_of: dict[str, Row] = {}
        self.procedures: list[Procedure] = []
        self.state: dict[str, _State] = {}
        self._setup_flow(patients, truths, perio, former_ids)
        self.setup_treatment(teeth, conds, perio, plan, configs_dir)
        self._setup_start()

    # setup

    def _setup_flow(self, patients: list[Patient], truths: list[Truth], perio: list[Perio], former_ids: set[str]):
        """Join and leave dates, by household."""
        flow, rng, start = self.flow, self.rng, self.start
        maintenance = {x.patient_id: x.maintenance for x in perio}
        by_id = {p.patient_id: p for p in patients}

        def reached(p: Patient) -> bool:
            contact = by_id.get(p.guardian_id, p) if p.age < 18 else p
            return contact.sms_consent or contact.email_on_file

        houses: dict[str, list[tuple[Patient, Truth]]] = {}
        for p, t in zip(patients, truths, strict=True):
            houses.setdefault(p.household_id, []).append((p, t))
        weights = flow["new_patient_month_weights"]
        current_days, current_cum = _window_days(start, SIM_TODAY, weights, 0.5)
        former_days, former_cum = _window_days(start, SIM_TODAY - timedelta(days=150), weights, 0.0)
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
                knows = bool(r) and rng.random() < flow["office_learns_reason"][r]
                self.state[p.patient_id] = _State(
                    p, t, reached(p), maintenance.get(p.patient_id, False), joined, left_on, r, knows
                )

    def _setup_start(self) -> None:
        """Established patients are mid-cycle at the start of the window; new patients call in when they join."""
        recall, rng, start = self.recall, self.rng, self.start
        first_age = self.flow["first_visit_age"]
        for pid, st in self.state.items():
            c = st.truth.recall_compliance
            birth = date.fromisoformat(st.patient.birth_date)
            if st.joined_on >= start or age_at(birth, start) < first_age:
                self.push(max(st.joined_on, start), REQUEST, pid, {"new": True})
            elif rng.random() < 0.15 * (1 - c):
                st.lapsed_on = start - timedelta(days=rng.randint(30, 700))
                st.last_visit = st.last_any = st.lapsed_on - timedelta(days=180)
            else:
                cycle = 90 if st.maintenance else 182
                st.last_visit = st.last_any = start - timedelta(days=rng.randint(0, cycle))
                st.last_bitewings = st.last_visit - timedelta(days=rng.choice([0, 182]))
                st.last_full_xrays = st.last_visit - timedelta(days=rng.randint(0, 5 * 365))
                months = recall["perio_maintenance_months"] if st.maintenance else recall["months"]
                due = add_months(st.last_visit, months)
                if rng.random() < recall["preappoint_base"] + recall["preappoint_per_compliance"] * c:
                    self.push(start, REQUEST, pid, {"earliest": max(due, start)})
                else:
                    self.self_book(st, due, start - timedelta(days=1))
            if st.left_on:
                self.push(st.left_on, LEAVE, pid)
        d = date(start.year, start.month, 1)
        while d < SIM_TODAY:
            self.push(d, PAIN, "pain")
            d = add_months(d, 1)

    # events and bookings

    def push(self, d: date, prio: int, key: str, payload: Row | None = None) -> None:
        self.seq += 1
        heapq.heappush(self.events, (d, prio, self.seq, key, payload or {}))

    def book(self, st: _State, d: date, slot: tuple, kind: str, codes: str, column: str, payload: Row) -> Appointment:
        day, prov, t, length = slot
        self.cal.book(day, prov, t, length)
        st.busy.setdefault(day, []).append((t, t + length))
        aid = f"A{len(self.appts) + 1:07d}"
        a = Appointment(
            aid,
            st.patient.patient_id,
            prov,
            column,
            day.isoformat(),
            hhmm(t),
            hhmm(t + length),
            kind,
            codes,
            d.isoformat(),
            "scheduled",
            "",
            payload.get("rebook_of", ""),
        )
        self.appts[aid] = a
        self.payload_of[aid] = {k: v for k, v in payload.items() if k not in ("earliest", "rebook_of")}
        lead = (day - d).days
        out = self.out_cfg
        p_early = out["early_cancel_base"] + out["early_cancel_per_noncompliance"] * (1 - st.truth.recall_compliance)
        if lead > out["booked_far_out_days"]:
            p_early += out["early_cancel_far_out_extra"]
        if kind != "emergency" and lead >= 3 and self.rng.random() < p_early:
            self.push(d + timedelta(days=self.rng.randint(1, lead - 2)), CANCEL, aid)
        self.push(day, VISIT, aid)
        return a

    def cancel(self, a: Appointment, d: date, status: str, cause: str) -> None:
        a.status, a.status_on = status, d.isoformat()
        self.causes[a.appt_id] = cause
        st = self.state[a.patient_id]
        if st.future_appt == a.appt_id:
            st.future_appt = ""
        if status == "cancelled":
            day, start, end = date.fromisoformat(a.date), to_min(a.start), to_min(a.end)
            self.cal.free(day, a.provider_id, start, end - start)
            st.busy[day].remove((start, end))

    def rebook_or_drift(self, a: Appointment, d: date, p_rebook: float, rebook_on: date) -> None:
        """After a missed visit: the office gets them back on the books, or they drift."""
        st = self.state[a.patient_id]
        again = self.rng.random() < p_rebook
        if a.kind in HYGIENE_KINDS:
            if again:
                self.push(rebook_on, REQUEST, a.patient_id, {"rebook_of": a.appt_id})
            else:
                self.self_book(st, date.fromisoformat(a.date), d)
        elif a.kind == "emergency":
            return
        else:
            self.treatment_missed(a, d, rebook_on if again else None)

    # hygiene

    def kind_for(self, st: _State, d: date, new: bool) -> str:
        if new:
            return "new_patient"
        age = age_at(date.fromisoformat(st.patient.birth_date), d)
        if st.maintenance and age >= 18:
            return "perio_maintenance"
        return "recall_child" if age < self.visits["child_cleaning_under"] else "recall_adult"

    def codes_for(self, st: _State, kind: str, d: date) -> str:
        visits = self.visits
        child = age_at(date.fromisoformat(st.patient.birth_date), d) < visits["child_cleaning_under"]
        if kind == "new_patient":
            return "D0150 D0210 " + ("D1120 D1206" if child else "D1110")
        codes = ["D0120", {"recall_adult": "D1110", "recall_child": "D1120", "perio_maintenance": "D4910"}[kind]]
        if not st.last_bitewings or (d - st.last_bitewings).days >= 30 * visits["bitewings_every_months"]:
            codes.append("D0274")
        if not st.last_full_xrays or (d - st.last_full_xrays).days >= 365 * visits["full_xrays_every_years"]:
            codes.append("D0210")
        if kind == "recall_child":
            codes.append("D1206")
        return " ".join(codes)

    def self_book(self, st: _State, due: date, today: date) -> None:
        """Patient books on their own, late or never; the office's reminder nudges a little."""
        recall, season, rng = self.recall, self.season, self.rng
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
        self.push(max(req, today + timedelta(days=1)), REQUEST, st.patient.patient_id)

    def next_recall(self, st: _State, d: date) -> None:
        recall, rng = self.recall, self.rng
        months = recall["perio_maintenance_months"] if st.maintenance else recall["months"]
        due = add_months(d, months)
        earliest = due - timedelta(days=rng.randint(0, 10))
        kid = age_at(date.fromisoformat(st.patient.birth_date), due) < 18
        if kid and due.month in (6, 7, 9) and rng.random() < self.season["back_to_school_pull_child"]:
            earliest = max(date(due.year, 8, rng.randint(1, 20)), d + timedelta(days=120))
        if rng.random() < recall["preappoint_base"] + recall["preappoint_per_compliance"] * st.truth.recall_compliance:
            self.push(d, REQUEST, st.patient.patient_id, {"earliest": earliest})
        else:
            self.self_book(st, due, d)

    def request_hygiene(self, d: date, pid: str, payload: Row) -> None:
        st, rng, recall = self.state[pid], self.rng, self.recall
        if st.left or st.future_appt or (st.lapsed_on and not payload):
            return
        birth = date.fromisoformat(st.patient.birth_date)
        first_age = self.flow["first_visit_age"]
        ready = date(birth.year + first_age, birth.month, min(birth.day, 28))
        if d < ready:
            self.push(ready + timedelta(days=rng.randint(0, 120)), REQUEST, pid, payload)
            return
        new = st.last_visit is None
        earliest = payload.get("earliest") or d + timedelta(days=rng.randint(2, 10) if new else rng.randint(1, 14))
        earliest = max(earliest, d + timedelta(days=1))
        if new and earliest.month == 12 and earliest.day >= 15 and rng.random() < self.season["december_postpone"]:
            earliest = date(earliest.year + 1, 1, rng.randint(3, 15))  # "let's do it after the holidays"
        kind = self.kind_for(st, earliest, new)
        pref = st.patient.preferred_time
        if pref == "after_school" and earliest.month in (7, 8):
            pref = "any"  # school is out
        slot = self.cal.find(earliest, self.visits["minutes"][kind], pref, "hygiene", None, st.busy)
        if slot is None or slot[0] > self.last_bookable:
            if d + timedelta(days=30) < SIM_TODAY:
                self.push(d + timedelta(days=30), REQUEST, pid, payload)
            return
        wait = (slot[0] - earliest).days
        if (
            "earliest" not in payload
            and wait > recall["booked_out_days"]
            and rng.random() < recall["booked_out_decline"]
        ):
            # the first opening is weeks away: "call me if something opens up", and some never call back
            if rng.random() < recall["booked_out_drift"]:
                st.lapsed_on = d
            elif d + timedelta(days=45) < SIM_TODAY:
                self.push(d + timedelta(days=rng.randint(30, 75)), REQUEST, pid, payload)
            return
        a = self.book(st, d, slot, kind, self.codes_for(st, kind, slot[0]), "hygiene", payload)
        st.future_appt = a.appt_id

    # visits

    def visit(self, d: date, aid: str) -> None:
        a = self.appts[aid]
        if a.status != "scheduled":
            return
        st, out, season, rng = self.state[a.patient_id], self.out_cfg, self.season, self.rng
        if st.left:
            # the office never heard they left: half never answer the confirmation calls and the slot is
            # cancelled the day before, the rest simply do not show up
            if rng.random() < 0.5:
                self.cancel(a, d - timedelta(days=1), "cancelled", "left_practice")
            else:
                st.no_shows += 1
                self.cancel(a, d, "no_show", "left_practice")
            self.rebook_or_drift(a, d, 0.0, d)
            return
        if d in self.storm_closed:
            self.cancel(a, d, "cancelled", "office_closed")
            self.rebook_or_drift(a, d, 0.9, d + timedelta(days=1))
            return
        t = st.truth
        ns, lc, cause = 1.0, 1.0, "patient"
        if st.reminded:
            ns *= out["office_reminder_no_show_factor"]
            lc *= out["office_reminder_late_cancel_factor"]
        if a.kind == "new_patient":
            ns *= out["new_patient_no_show_factor"]
        if a.kind == "emergency":
            ns *= out["emergency_no_show_factor"]
            lc *= out["emergency_no_show_factor"]
        if (d - date.fromisoformat(a.booked_on)).days > out["booked_far_out_days"]:
            ns *= out["booked_far_out_factor"]
        ns *= season["no_show_day_of_week"].get(DAYS[d.weekday()], 1.0)
        if d.weekday() == 4 and d.month in (6, 7, 8):
            ns *= season["summer_friday_factor"]
            cause = "summer_friday"
        if holiday_week(d):
            ns *= season["holiday_week_factor"]
            cause = "holiday_week"
        if d in self.bad_days:
            ns *= season["weather_no_show_factor"]
            lc *= season["weather_late_cancel_factor"]
            cause = "weather"
        r = rng.random()
        c = t.recall_compliance
        if r < t.no_show_rate * ns:
            st.no_shows += 1
            self.cancel(a, d, "no_show", cause)
            p = out["rebook_after_no_show_base"] + out["rebook_after_no_show_per_compliance"] * c
            self.rebook_or_drift(a, d, p, d + timedelta(days=rng.randint(2, 21)))
        elif r < t.no_show_rate * ns + t.late_cancel_rate * lc:
            st.late_cancels += 1
            self.cancel(a, d - timedelta(days=rng.choice([0, 1])), "late_cancel", cause)
            self.rebook_or_drift(a, d, out["reschedule_after_late_cancel"], d + timedelta(days=rng.randint(3, 30)))
        else:
            self.complete(a, d)

    def complete(self, a: Appointment, d: date) -> None:
        st = self.state[a.patient_id]
        a.status, a.status_on = "completed", d.isoformat()
        if st.future_appt == a.appt_id:
            st.future_appt = ""
        st.completed += 1
        st.first_visit = st.first_visit or d
        st.last_any = d
        if a.kind in TREATMENT_KINDS:
            self.complete_treatment(a, d)
            return
        tooth = self.payload_of[a.appt_id].get("tooth", "") if a.kind == "emergency" else ""
        for code in a.codes.split():
            self.procedures.append(Procedure(a.appt_id, a.patient_id, d.isoformat(), a.provider_id, code, tooth, ""))
        if a.kind == "emergency":
            self.complete_emergency(a, d)
            return
        st.last_visit = d
        if "D0274" in a.codes:
            st.last_bitewings = d
        if "D0210" in a.codes:
            st.last_full_xrays = d
        self.exam(st, d)
        self.next_recall(st, d)

    # the clock

    def play(self) -> None:
        while self.events and self.events[0][0] < SIM_TODAY:
            d, prio, _, key, payload = heapq.heappop(self.events)
            if prio == LEAVE:
                st = self.state[key]
                st.left = True
                if st.office_knows:
                    for a in self.appts.values():
                        if a.patient_id == key and a.status == "scheduled":
                            self.cancel(a, d, "cancelled", "left_practice")
                            self.rebook_or_drift(a, d, 0.0, d)
            elif prio == CANCEL:
                a = self.appts[key]
                if a.status == "scheduled":
                    self.cancel(a, d, "cancelled", "patient")
                    self.rebook_or_drift(a, d, self.out_cfg["reschedule_after_early_cancel"], d)
            elif prio == PAIN:
                self.pain_check(d)
            elif prio == VISIT:
                self.visit(d, key)
            else:
                kind = payload.get("kind", "hygiene")
                if kind == "hygiene":
                    self.request_hygiene(d, key, payload)
                elif kind == "emergency":
                    self.request_emergency(d, key, payload)
                else:
                    self.request_treatment(d, key, payload)
        self.finish_treatment()

    def results(
        self,
    ) -> tuple[list[Appointment], list[AppointmentTruth], list[Procedure], list[PatientHistory], list[FlowTruth]]:
        """What the office's system shows on SIM_TODAY, and the hidden story behind it."""
        nxt: dict[str, str] = {}
        for a in self.appts.values():
            if a.status == "scheduled" and (a.patient_id not in nxt or a.date < nxt[a.patient_id]):
                nxt[a.patient_id] = a.date
        history, flows = [], []
        inactive_after = SIM_TODAY - timedelta(days=548)  # offices call a patient inactive after 18 months
        for pid, st in self.state.items():
            cleaning = self.appts.get(st.future_appt)
            if st.left and st.office_knows:
                status = "archived"
            elif pid in nxt or (st.last_any and st.last_any >= inactive_after):
                status = "active"
            else:
                status = "inactive"
            first = st.first_visit or (st.joined_on if st.joined_on < self.start else None)
            history.append(
                PatientHistory(
                    pid,
                    first.isoformat() if first else "",
                    st.last_any.isoformat() if st.last_any else "",
                    st.last_visit.isoformat() if st.last_visit else "",
                    nxt.get(pid, ""),
                    cleaning.date if cleaning else "",
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
                    st.turned_away,
                )
            )
        appointments = sorted(self.appts.values(), key=lambda a: (a.date, a.start, a.provider_id))
        truth_rows = [AppointmentTruth(a.appt_id, self.causes.get(a.appt_id, "")) for a in appointments]
        procedures = sorted(self.procedures, key=lambda p: (p.date, p.appt_id))
        return appointments, truth_rows, procedures, history, flows


def run(
    office: Row,
    patients: list[Patient],
    truths: list[Truth],
    teeth: list[Tooth],
    conds: list[Condition],
    perio: list[Perio],
    plan: list[PlanItem],
    configs_dir: Path,
    former_ids: set[str],
    seed: int = 42,
) -> tuple[list[Appointment], list[AppointmentTruth], list[Procedure], list[PatientHistory], list[FlowTruth]]:
    """Play out the history. Updates conds, perio and plan in place (found, treated, statuses)."""
    practice = Practice(office, patients, truths, teeth, conds, perio, plan, configs_dir, former_ids, seed)
    practice.play()
    return practice.results()


def overdue_share(history: list[PatientHistory], flows: list[FlowTruth], perio: list[Perio]) -> float:
    """Share of active patients past due for a cleaning with nothing booked: the recall gap Sadhana works."""
    gone = {f.patient_id for f in flows if f.left_on}
    maint = {x.patient_id for x in perio if x.maintenance}
    active = [h for h in history if h.status == "active" and h.patient_id not in gone and h.last_cleaning]
    overdue = 0
    for h in active:
        months = 3 if h.patient_id in maint else 6
        if not h.next_cleaning and date.fromisoformat(h.last_cleaning) < add_months(SIM_TODAY, -months - 1):
            overdue += 1
    return overdue / len(active)


def utilization(appointments: list[Appointment], office: Row, column: str = "hygiene", months: int = 12) -> float:
    """Booked minutes kept or held through the day (completed, no-show, late cancel) over capacity.
    Dentists also spend a few minutes on the exam in every completed cleaning, which counts as doctor time."""
    cal = Calendar(office, holidays(range(SIM_TODAY.year - 2, SIM_TODAY.year + 1)))
    lo = add_months(SIM_TODAY, -months)
    cap = sum(cal.capacity(lo + timedelta(days=i), column) for i in range((SIM_TODAY - lo).days))
    used = sum(
        to_min(a.end) - to_min(a.start) + cal.turnover
        for a in appointments
        if a.column == column
        and a.status in ("completed", "no_show", "late_cancel")
        and lo <= date.fromisoformat(a.date) < SIM_TODAY
    )
    if column == "doctor":
        exams = sum(
            a.kind in HYGIENE_KINDS and a.status == "completed" and lo <= date.fromisoformat(a.date) < SIM_TODAY
            for a in appointments
        )
        used += exams * int(office.get("doctor_check_min_in_hygiene", 10))
    return used / cap if cap else 0.0


def write(
    appointments: list[Appointment],
    appt_truth: list[AppointmentTruth],
    procedures: list[Procedure],
    history: list[PatientHistory],
    flows: list[FlowTruth],
    out_dir: Path,
) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    tables: dict[str, list] = {
        "appointments.csv": appointments,
        "appointment_truth.csv": appt_truth,
        "procedures.csv": procedures,
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
