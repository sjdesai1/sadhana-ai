"""Simulator part 3b: treatment history.

Runs inside the same day-by-day clock as the hygiene history (history.Practice mixes this in):
- exams: the chart's diagnosed problems are presented at the patient's first exam in the window; later exams
  find problems that were missed before and new decay that formed since
- each problem group (one tooth, all gum quadrants, a night guard, whitening) is accepted, declined, or left
  pending; patients weigh priority, what they pay after insurance, trust, financing, cosmetic interest and the
  time of year (use-it-or-lose-it benefits in the fall, deductibles resetting in January)
- accepted work is booked with the right provider: hygienists for deep cleanings, a dentist who is allowed to do
  it, or a visiting specialist; work the office cannot do is referred out
- real sequencing: build-up and crown after the root canal, implant crown about 3 months after the implant,
  crown and night guard seat visits after the lab, aligner check-ins every 6 weeks
- untreated deep decay and cracks start hurting: emergency visits, which bring some lapsed patients back
- missed treatment visits drop the work back to "accepted, not scheduled", the pile the product chases
"""

from __future__ import annotations

import csv
import math
import random
from datetime import date, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any

from sadhana.economics.engine import can_perform
from sadhana.simulator.charts import Condition, PlanItem, _filling_code, _root_canal_code
from sadhana.simulator.events import Coverage, in_window
from sadhana.simulator.schedule import Appointment, Calendar, Procedure

if TYPE_CHECKING:
    from sadhana.simulator.history import _State

Row = dict[str, Any]
SRP = {"D4341", "D4342", "D4355"}
ROOT_CANAL = {"D3310", "D3320", "D3330"}
AFTER_ROOT_CANAL = {"D2950", "D2740"}
IMPLANT_RESTORE = {"D6057", "D6065"}
COSMETIC = {"D9972", "D8090", "D2962"}
PRIORITY = {"urgent": 0, "needed": 1, "elective": 2}
OPEN = {"proposed", "accepted", "scheduled"}
DONE = {"completed", "declined", "referred"}
FOLLOWUP_MIN = 20
REQUEST = 4  # same event priority as history.REQUEST


def items_for(c: Condition) -> list[tuple[str, str, str]]:
    """(code, reason, priority) for a problem found at a later exam or an emergency."""
    if not c.tooth.isdigit():
        return [("D2391", "decay on baby tooth", "needed")] if c.condition == "caries" else []
    n = int(c.tooth)
    if (c.condition == "caries" and c.severity == "deep") or c.condition == "periapical":
        reason = "deep decay into the nerve" if c.condition == "caries" else "infection at the root"
        return [
            (_root_canal_code(n), reason, "urgent"),
            ("D2950", "build up after root canal", "urgent"),
            ("D2740", "crown after root canal", "urgent"),
        ]
    if c.condition == "caries":
        return [(_filling_code(n, 1 if c.severity == "early" else 2), "decay", "needed")]
    if c.condition in ("cracked", "failing_restoration"):
        return [("D2740", c.condition.replace("_", " "), "needed")]
    if c.condition == "impacted":
        return [("D7240", "impacted wisdom tooth", "needed")]
    return []


def _sigmoid(x: float) -> float:
    return 1 / (1 + math.exp(-x))


def load_catalog(configs_dir: Path) -> dict[str, Row]:
    with (configs_dir / "catalog" / "service_catalog.csv").open(newline="") as f:
        return {r["code"]: r for r in csv.DictReader(f)}


def plan_value(plan: list[PlanItem], office: Row, catalog: dict[str, Row], patient_ids: set[str]) -> dict[str, float]:
    """Plan value at the office's full fee, by status, for the given patients."""
    out: dict[str, float] = {}
    for x in plan:
        if x.patient_id in patient_ids:
            fee = float(catalog[x.code]["office_fee"]) * float(office["fee_multiplier"])
            out[x.status] = out.get(x.status, 0.0) + fee
    return out


class TreatmentMixin:
    """Treatment half of the practice. Expects the attributes and methods history.Practice sets up."""

    office: Row
    rng: random.Random
    cal: Calendar
    state: dict[str, _State]
    start: date
    last_bookable: date
    tx: Row
    appts: dict[str, Appointment]
    payload_of: dict[str, Row]
    procedures: list[Procedure]
    coverage: Coverage
    scenarios: dict[str, Row]

    def push(self, d: date, prio: int, key: str, payload: Row | None = None) -> None: ...
    def book(self, st: _State, d: date, slot: tuple, kind: str, codes: str, column: str, payload: Row) -> Appointment:
        raise NotImplementedError

    # setup

    def setup_treatment(
        self, teeth: list, conds: list[Condition], perio: list, plan: list[PlanItem], configs_dir: Path
    ) -> None:
        self.catalog = load_catalog(configs_dir)
        state_code = self.office["state"]
        with (configs_dir / "payers" / f"payers_{state_code}.csv").open(newline="") as f:
            self.payers = {r["payer"]: r for r in csv.DictReader(f)}
        with (configs_dir / "payers" / f"medicaid_fees_{state_code}.csv").open(newline="") as f:
            self.medicaid = {r["code"]: r for r in csv.DictReader(f)}
        self.plan = plan
        self.conds = conds
        self.perio_of = {x.patient_id: x for x in perio}
        self.items_of: dict[str, list[PlanItem]] = {}
        self.plan_by_id: dict[str, PlanItem] = {}
        for i, x in enumerate(plan):
            x.plan_id = f"T{i + 1:06d}"
            self.items_of.setdefault(x.patient_id, []).append(x)
            self.plan_by_id[x.plan_id] = x
        self.conds_of: dict[str, list[Condition]] = {}
        for c in conds:
            self.conds_of.setdefault(c.patient_id, []).append(c)
        self.teeth_of: dict[str, list[str]] = {}
        self.big_fillings_of: dict[str, list[str]] = {}
        for t in teeth:
            if t.present and t.restoration in ("none", "filling"):
                self.teeth_of.setdefault(t.patient_id, []).append(t.tooth)
            if t.present and t.restoration == "filling" and t.filling_surfaces >= 2:
                self.big_fillings_of.setdefault(t.patient_id, []).append(t.tooth)
        self._routes: dict[str, tuple[str, set[str]] | None] = {}

    # money and routing

    def fee(self, code: str) -> float:
        return float(self.catalog[code]["office_fee"]) * float(self.office["fee_multiplier"])

    def out_of_pocket(self, st: _State, code: str, d: date) -> float:
        payer = self.payers[self.coverage.payer_on(st.patient.patient_id, d)]
        ptype = payer["payer_type"]
        if ptype == "self_pay":
            return self.fee(code) * (1 - float(self.office.get("cash_discount", 0)))
        if ptype == "medicaid":
            m = self.medicaid.get(code)
            return 0.0 if m and m["covered"] == "1" else self.fee(code)
        cls = self.catalog[code]["benefit_class"]
        allowed = self.fee(code) * float(payer.get(f"allowed_pct_{cls}", 1.0) or 1.0)
        cover = float(payer.get(f"coverage_{cls}", 0) or 0)
        return allowed * (1 - cover)

    def route(self, code: str) -> tuple[str, set[str]] | None:
        """Which column and providers can do this; None means the office refers it out."""
        if code not in self._routes:
            proc, staff = self.catalog[code], self.office["staff"]
            ok = {s["id"] for s in staff if can_perform(s, proc, self.office)[0]}
            spec = {s["id"] for s in self.cal.columns["specialist"]} & ok
            hyg = {s["id"] for s in self.cal.columns["hygiene"]} & ok
            doc = {s["id"] for s in self.cal.columns["doctor"]} & ok
            if spec:
                self._routes[code] = ("specialist", spec)
            elif code in SRP and hyg:
                self._routes[code] = ("hygiene", hyg)
            elif doc:
                self._routes[code] = ("doctor", doc)
            else:
                self._routes[code] = None
        return self._routes[code]

    def first_minutes(self, code: str) -> int:
        row = self.catalog[code]
        visits = int(row["visits"])
        return max(FOLLOWUP_MIN, int(row["chair_min"]) - FOLLOWUP_MIN * (visits - 1))

    # exams and decisions

    def add_items(self, st: _State, c: Condition, d: date) -> list[PlanItem]:
        open_codes = {
            x.code for x in self.items_of.get(st.patient.patient_id, []) if x.tooth == c.tooth and x.status in OPEN
        }
        new = []
        for code, reason, priority in items_for(c):
            if code in open_codes:
                continue
            x = PlanItem(st.patient.patient_id, c.tooth, code, reason, priority, "proposed")
            x.plan_id = f"T{len(self.plan) + 1:06d}"
            x.diagnosed_on = d.isoformat()
            self.plan.append(x)
            self.plan_by_id[x.plan_id] = x
            self.items_of.setdefault(st.patient.patient_id, []).append(x)
            new.append(x)
        return new

    def exam(self, st: _State, d: date) -> None:
        """A dentist exam during a cleaning or new-patient visit: findings, then the plan is presented."""
        pid, tx = st.patient.patient_id, self.tx
        today = d.isoformat()
        conds = self.conds_of.setdefault(pid, [])
        if not st.presented:
            st.presented = True
            for x in self.items_of.get(pid, []):
                if x.status == "proposed" and not x.diagnosed_on:
                    x.diagnosed_on = today
            for c in conds:
                if c.diagnosed and not c.found_on:
                    c.found_on = today
        else:
            for c in conds:
                missed = not c.diagnosed and not c.treated_on and c.condition != "missing_unreplaced"
                if missed and self.rng.random() < tx["missed_found_later"]:
                    c.diagnosed, c.found_on = True, today
                    self.add_items(st, c, d)
            self.new_findings(st, d, conds)
        st.last_exam = d
        self.present(st, d)

    def new_findings(self, st: _State, d: date, conds: list[Condition]) -> None:
        """What changed since the last exam: new decay, old fillings breaking down, gum disease starting."""
        pid, tx, rng, today = st.patient.patient_id, self.tx, self.rng, d.isoformat()
        months = min(24.0, (d - st.last_exam).days / 30) if st.last_exam else 6.0
        scale = months / 6

        def add(tooth: str, condition: str, severity: str) -> None:
            c = Condition(pid, tooth, condition, severity, True, today)
            self.conds.append(c)
            conds.append(c)
            self.add_items(st, c, d)

        lesions = 0
        budget = rng.random()
        p = math.exp(-tx["new_decay_per_6_months"] * st.truth.caries_risk * scale)
        cum = p
        while budget > cum and lesions < 4:  # Poisson draw
            lesions += 1
            p *= tx["new_decay_per_6_months"] * st.truth.caries_risk * scale / lesions
            cum += p
        busy = {c.tooth for c in conds if not c.treated_on}
        for _ in range(lesions):
            options = [t for t in self.teeth_of.get(pid, []) if t not in busy]
            if not options:
                break
            tooth = rng.choice(options)
            busy.add(tooth)
            add(tooth, "caries", "early" if rng.random() < 0.6 else "moderate")
        for tooth in list(self.big_fillings_of.get(pid, [])):
            if tooth not in busy and rng.random() < tx["big_filling_fails_per_exam"] * scale:
                self.big_fillings_of[pid].remove(tooth)
                add(tooth, "failing_restoration" if rng.random() < 0.75 else "cracked", "moderate")
        perio = self.perio_of.get(pid)
        adult = st.patient.age >= 25
        has_srp = any(x.code in SRP and x.status in OPEN for x in self.items_of.get(pid, []))
        onset = tx["perio_onset_per_exam"] * st.truth.perio_risk * scale
        if adult and perio and not st.maintenance and not has_srp and rng.random() < onset:
            perio.charted_stage = "stage_2"
            for q in ("Q1", "Q2", "Q3", "Q4"):
                x = PlanItem(pid, q, "D4341", "scaling and root planing", "needed", "proposed")
                x.plan_id, x.diagnosed_on = f"T{len(self.plan) + 1:06d}", today
                self.plan.append(x)
                self.plan_by_id[x.plan_id] = x
                self.items_of.setdefault(pid, []).append(x)

    def present(self, st: _State, d: date, boost: float = 0.0, tooth: str | None = None) -> None:
        """Talk through the open plan; each problem group is accepted, declined or left for later."""
        pid, t, tx = st.patient.patient_id, st.truth, self.tx
        today = d.isoformat()
        items = [
            x
            for x in self.items_of.get(pid, [])
            if x.status == "proposed" and x.diagnosed_on and (tooth is None or x.tooth == tooth)
        ]
        groups: dict[str, list[PlanItem]] = {}
        for x in items:
            key = "srp" if x.code in SRP else x.tooth if x.tooth and not x.tooth.startswith("Q") else x.code
            groups.setdefault(key, []).append(x)
        insured = self.payers[self.coverage.payer_on(pid, d)]["payer_type"] in ("ppo", "premier")
        shift = {p: tx.get("accept_shift", 0.0) for p in PRIORITY}
        for sc in self.scenarios.values():
            if "accept_shift" in sc and in_window(sc, d):
                for p, v in sc["accept_shift"].items():
                    shift[p] += v
        visit_mood = self.rng.gauss(0, 0.5)
        for group in groups.values():
            for x in group:
                x.times_presented += 1
            if any(self.route(x.code) is None for x in group):
                for x in group:
                    x.status, x.status_on = "referred", today
                continue
            pri = min((x.priority for x in group), key=PRIORITY.__getitem__)
            cost = sum(self.out_of_pocket(st, x.code, d) for x in group) / tx["cost_scale"]
            cost *= 1 - tx["financing_softens"] * t.financing_propensity
            z = (
                tx["accept_base"][pri]
                + tx["accept_trust"] * (t.trust_baseline - 0.5)
                - tx["accept_price"] * t.price_sensitivity * min(cost, 2.5)
                - tx["repeat_penalty"] * (max(x.times_presented for x in group) - 1)
                + visit_mood
                + boost
                + shift[pri]
            )
            if any(x.code in COSMETIC for x in group):
                z += tx["cosmetic_interest"] * (t.cosmetic_interest - 0.5)
            if insured and d.month in (10, 11, 12):
                z += tx["year_end_insured_boost"]
            if insured and d.month == 1:
                z -= tx["january_reset_penalty"]
            if self.rng.random() < _sigmoid(z):
                for x in group:
                    x.status, x.status_on = "accepted", today
                self.schedule_ready(st, d, group)
            elif self.rng.random() < tx["decline_outright"]:
                for x in group:
                    x.status, x.status_on = "declined", today

    # booking

    def blocked(self, x: PlanItem) -> bool:
        if x.code in AFTER_ROOT_CANAL:
            needs = ROOT_CANAL
        elif x.code in IMPLANT_RESTORE:
            needs = {"D6010"}
        else:
            return False
        return any(y.tooth == x.tooth and y.code in needs and y.status not in DONE for y in self.items_of[x.patient_id])

    def schedule_ready(self, st: _State, d: date, items: list[PlanItem], earliest: date | None = None) -> None:
        """Bundle accepted, unblocked work into visits per column and ask for slots."""
        tx = self.tx
        ready = sorted(
            (x for x in items if x.status == "accepted" and not self.blocked(x)),
            key=lambda x: (PRIORITY[x.priority], x.tooth, x.code),
        )
        bundles: dict[tuple[str, tuple[str, ...]], list[PlanItem]] = {}
        for x in ready:
            r = self.route(x.code)
            if r is not None:
                bundles.setdefault((r[0], tuple(sorted(r[1]))), []).append(x)
        for (column, allowed), xs in bundles.items():
            visits: list[list[PlanItem]] = [[]]
            minutes = 0
            for x in xs:
                m = self.first_minutes(x.code)
                if visits[-1] and minutes + m > tx["max_visit_minutes"]:
                    visits.append([])
                    minutes = 0
                visits[-1].append(x)
                minutes += m
            for v in visits:
                pri = min((x.priority for x in v), key=PRIORITY.__getitem__)
                lo, hi = tx["lead_days"][pri]
                payload = {
                    "kind": "srp" if column == "hygiene" else "treatment",
                    "column": column,
                    "allowed": list(allowed),
                    "items": [x.plan_id for x in v],
                    "earliest": earliest or d + timedelta(days=self.rng.randint(lo, hi)),
                }
                if earliest:
                    payload["not_before"] = earliest  # healing or lab time; kept if the visit is rebooked
                self.push(d, REQUEST, st.patient.patient_id, payload)

    def request_treatment(self, d: date, pid: str, payload: Row) -> None:
        st, tx = self.state[pid], self.tx
        if st.left:
            return
        kind = payload["kind"]
        if kind == "followup":
            items: list[PlanItem] = []
            minutes, codes = FOLLOWUP_MIN, ""
        else:
            items = [self.plan_by_id[i] for i in payload["items"]]
            items = [x for x in items if x.status == "accepted"]
            if not items:
                return
            minutes = sum(self.first_minutes(x.code) for x in items)
            codes = " ".join(x.code for x in items)
        earliest = max(payload.get("earliest") or d, payload.get("not_before") or d, d + timedelta(days=1))
        pref = st.patient.preferred_time
        slot = self.cal.find(earliest, minutes, pref, payload["column"], set(payload["allowed"]), st.busy)
        if slot is None or slot[0] > self.last_bookable:
            if d + timedelta(days=30) < self.last_bookable:
                self.push(d + timedelta(days=30), REQUEST, pid, {**payload, "earliest": None})
            return
        urgent = any(x.priority == "urgent" for x in items)
        wait = (slot[0] - earliest).days
        booked_out = items and not urgent and not payload.get("rebook_of") and wait > tx["booked_out_days"]
        if booked_out and self.rng.random() < tx["booked_out_drift"]:
            return  # "I'll call when my schedule frees up": accepted, never booked
        a = self.book(st, d, slot, kind, codes, payload["column"], payload)
        for x in items:
            x.status, x.status_on, x.appt_id = "scheduled", d.isoformat(), a.appt_id

    def treatment_missed(self, a: Appointment, d: date, rebook_on: date | None) -> None:
        for x in self.items_of.get(a.patient_id, []):
            if x.appt_id == a.appt_id and x.status == "scheduled":
                x.status, x.status_on = "accepted", d.isoformat()
        if rebook_on:
            self.push(rebook_on, REQUEST, a.patient_id, {**self.payload_of[a.appt_id], "rebook_of": a.appt_id})

    def complete_treatment(self, a: Appointment, d: date) -> None:
        st, tx, today = self.state[a.patient_id], self.tx, d.isoformat()
        payload = self.payload_of[a.appt_id]
        if a.kind == "followup":
            left = int(payload.get("remaining", 1)) - 1
            if left > 0:
                gap = d + timedelta(days=tx["aligner_check_days"])
                nxt = {**payload, "remaining": left, "earliest": gap, "not_before": gap}
                self.push(d, REQUEST, a.patient_id, nxt)
            return
        done = [x for x in self.items_of.get(a.patient_id, []) if x.appt_id == a.appt_id and x.status == "scheduled"]
        for x in done:
            x.status, x.status_on = "completed", today
            self.procedures.append(Procedure(a.appt_id, a.patient_id, today, a.provider_id, x.code, x.tooth, x.plan_id))
            if x.code in SRP:
                st.maintenance = True
                if a.patient_id in self.perio_of:
                    self.perio_of[a.patient_id].maintenance = True
            elif x.tooth and not x.tooth.startswith("Q"):
                for c in self.conds_of.get(a.patient_id, []):
                    if c.tooth == x.tooth and not c.treated_on and c.condition != "missing_unreplaced":
                        c.treated_on = today
            visits = int(self.catalog[x.code]["visits"])
            if visits > 1:
                gap = int(self.catalog[x.code]["lab_days"]) or 14
                if x.code == "D8090":
                    gap = tx["aligner_check_days"]
                follow = {
                    "kind": "followup",
                    "column": a.column,
                    "allowed": [a.provider_id],
                    "remaining": visits - 1,
                    "earliest": d + timedelta(days=gap),
                    "not_before": d + timedelta(days=gap),
                }
                self.push(d, REQUEST, a.patient_id, follow)
            wait = {"D6010": tx["implant_healing_days"]}.get(x.code, tx["after_root_canal_days"])
            if x.code in ROOT_CANAL or x.code == "D6010":
                deps = [y for y in self.items_of[a.patient_id] if y.tooth == x.tooth and y.status == "accepted"]
                self.schedule_ready(st, d, deps, earliest=d + timedelta(days=wait))

    # emergencies

    def pain_check(self, d: date) -> None:
        """First of the month: untreated deep decay, infections and cracks start hurting, and some patients break
        a tooth or lose a filling out of the blue."""
        broken = self.tx["broken_tooth_per_month"]
        for pid, st in self.state.items():
            if st.left or st.joined_on > d or self.rng.random() >= broken:
                continue
            options = self.teeth_of.get(pid, [])
            if options and st.patient.age >= 12:
                when = d + timedelta(days=self.rng.randint(0, 27))
                self.push(when, REQUEST, pid, {"kind": "emergency", "tooth": self.rng.choice(options)})
        pain = self.tx["pain_per_month"]
        for c in self.conds:
            p = pain.get(c.severity if c.condition == "caries" else c.condition, 0.0)
            if not p or c.treated_on:
                continue
            st = self.state.get(c.patient_id)
            if st is None or st.left or st.joined_on > d or self.rng.random() >= p:
                continue
            when = d + timedelta(days=self.rng.randint(0, 27))
            self.push(when, REQUEST, c.patient_id, {"kind": "emergency", "tooth": c.tooth})

    def request_emergency(self, d: date, pid: str, payload: Row) -> None:
        st = self.state[pid]
        if st.left:
            return
        doctors = {s["id"] for s in self.cal.columns["doctor"]}
        slot = self.cal.find(d, 30, "any", "doctor", doctors, st.busy, horizon=3)
        if slot is None:
            st.turned_away += 1  # no opening in time: urgent care or another dentist
            return
        self.book(st, d, slot, "emergency", "D0140 D9110", "doctor", payload)

    def complete_emergency(self, a: Appointment, d: date) -> None:
        st, tx = self.state[a.patient_id], self.tx
        tooth = self.payload_of[a.appt_id]["tooth"]
        conds = self.conds_of.setdefault(a.patient_id, [])
        if not any(c.tooth == tooth and not c.treated_on for c in conds):
            kind = "cracked" if self.rng.random() < 0.5 else "failing_restoration"
            c = Condition(a.patient_id, tooth, kind, "moderate", True, d.isoformat())
            self.conds.append(c)
            conds.append(c)
        for c in conds:
            if c.tooth == tooth and not c.treated_on:
                if not c.diagnosed or not c.found_on:
                    c.diagnosed, c.found_on = True, c.found_on or d.isoformat()
                self.add_items(st, c, d)
        for x in self.items_of.get(a.patient_id, []):
            if x.tooth == tooth and x.status == "proposed" and not x.diagnosed_on:
                x.diagnosed_on = d.isoformat()
        self.present(st, d, boost=tx["emergency_accept_boost"], tooth=tooth)
        long_gone = st.last_visit is None or (d - st.last_visit).days > 270
        if not st.future_appt and long_gone and self.rng.random() < tx["emergency_reactivates"]:
            st.lapsed_on = None
            self.push(d, REQUEST, a.patient_id, {"reactivate": True})

    def finish_treatment(self) -> None:
        """Established patients never seen in the window keep the plan from their last visit before it.
        Patients never examined at all have no plan the office knows about."""
        for pid, st in self.state.items():
            if st.presented:
                continue
            before = st.joined_on < self.start and st.last_visit is not None
            stamp = st.last_visit.isoformat() if before and st.last_visit else ""
            for x in self.items_of.get(pid, []):
                if x.status == "proposed" and not x.diagnosed_on:
                    x.diagnosed_on = stamp
            for c in self.conds_of.get(pid, []):
                if c.diagnosed and not c.found_on:
                    if stamp:
                        c.found_on = stamp
                    else:
                        c.diagnosed = False
        self.plan[:] = [x for x in self.plan if x.diagnosed_on]
