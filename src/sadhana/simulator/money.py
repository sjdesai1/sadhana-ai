"""Simulator part 4: money.

Turns every completed procedure into what an office's billing system would hold:
- claims: what each payer allows and pays, PPO write-offs, the Medicaid fee schedule, secondary insurance,
  first-pass denials and resubmits, and how long payers take to pay
- benefits per patient per calendar year: annual max used and remaining, deductible, frequency limits
  (two cleanings a year, one crown per tooth in five years), waiting periods for major work, January resets
- patient portions: paid at the visit or later from a statement, third-party financing or an in-house payment
  plan for big bills, and some that are never paid
- cancellation fees per office policy (never for Medicaid), only some of them collected
- what is left on SIM_TODAY: balances by guarantor with aging, insurance still owed, and unused benefits

Observed: ledger.csv, claims.csv, payments.csv, benefits.csv, balances.csv. Payment behavior comes from each
guarantor's hidden traits (price sensitivity, income, financing propensity) and stays hidden.
"""

from __future__ import annotations

import csv
import json
import random
import re
from dataclasses import asdict, dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any

from sadhana.economics.engine import allowed_amount
from sadhana.simulator.population import SIM_TODAY
from sadhana.simulator.schedule import add_months
from sadhana.simulator.treatment import load_catalog

if TYPE_CHECKING:
    from sadhana.simulator.build import Sim

Row = dict[str, Any]
INSURED = {"ppo", "premier"}
COVERED_CLASSES = {"preventive", "basic", "major"}


@dataclass
class LedgerLine:
    appt_id: str
    patient_id: str
    guarantor_id: str
    date: str
    code: str
    tooth: str
    provider_id: str
    payer: str
    fee: float  # office's full fee
    allowed: float  # what the payer allows; for cash, the fee less the cash discount
    primary_paid: float
    secondary_paid: float
    patient_portion: float
    write_off: float  # contractual write-off plus anything the office could not collect from the payer
    note: str  # frequency, waiting period, not covered, elective, denied, annual max reached


@dataclass
class Claim:
    claim_id: str
    appt_id: str
    patient_id: str
    payer: str
    rank: str  # primary, secondary
    submitted_on: str
    status: str  # paid, paid_after_resubmit, written_off, pending
    paid_on: str
    billed: float
    expected: float
    paid: float
    denial_reason: str


@dataclass
class Payment:
    payment_id: str
    date: str
    guarantor_id: str
    patient_id: str
    source: str  # insurance, patient, financing, payment_plan, cancellation_fee
    amount: float
    ref: str  # claim or appointment


@dataclass
class Benefit:
    patient_id: str
    year: int
    payer: str
    rank: str
    annual_max: float
    used: float
    remaining: float
    deductible: float
    deductible_met: float


@dataclass
class Balance:
    guarantor_id: str
    balance: float
    current: float  # 0-30 days
    days_31_60: float
    days_61_90: float
    over_90: float
    insurance_pending: float
    on_payment_plan: bool


def load_billing(configs_dir: Path) -> Row:
    return json.loads((configs_dir / "simulator" / "clock.json").read_text())["billing"]


def _parse_freq(text: str) -> tuple[int, int | None, bool] | None:
    """'2 per 12 months' -> (2, 12, False); '1 per tooth lifetime' -> (1, None, True)."""
    if not text:
        return None
    m = re.match(r"(\d+) per (?:(tooth|quad) )?(?:per )?(?:(\d+) months|lifetime)", text)
    if not m:
        return None
    return int(m.group(1)), int(m.group(3)) if m.group(3) else None, bool(m.group(2))


class Biller:
    def __init__(self, sim: Sim, configs_dir: Path, seed: int) -> None:
        self.sim = sim
        self.office = sim.office
        self.cfg = load_billing(configs_dir)
        self.rng = random.Random(f"money-{sim.office['office_id']}-{seed}")
        self.catalog = load_catalog(configs_dir)
        state = sim.office["state"]
        with (configs_dir / "payers" / f"payers_{state}.csv").open(newline="") as f:
            self.payers = {r["payer"]: r for r in csv.DictReader(f)}
        with (configs_dir / "payers" / f"medicaid_fees_{state}.csv").open(newline="") as f:
            self.medicaid = {r["code"]: r for r in csv.DictReader(f)}
        self.patients = {p.patient_id: p for p in sim.patients}
        self.truths = {t.patient_id: t for t in sim.truths}
        self.joined = {f.patient_id: date.fromisoformat(f.joined_on) for f in sim.flows}
        self.used: dict[tuple[str, int, str], float] = {}
        self.deductible_met: dict[tuple[str, int, str], float] = {}
        self.history: dict[tuple[str, str, str], list[date]] = {}
        self.ledger: list[LedgerLine] = []
        self.claims: list[Claim] = []
        self.payments: list[Payment] = []
        self.charges: list[tuple[str, date, float, str]] = []  # guarantor, date, amount still owed, ref
        self.plans: set[str] = set()
        self.sim_start = min((a.date for a in sim.appointments), default=SIM_TODAY.isoformat())
        self.sim_start = date.fromisoformat(self.sim_start)

    # who pays

    def guarantor(self, pid: str) -> str:
        p = self.patients[pid]
        if p.guardian_id:
            return p.guardian_id
        if p.subscriber_id and p.subscriber_id != pid and p.subscriber_id in self.patients:
            return p.subscriber_id
        return pid

    def fee(self, code: str) -> float:
        return round(float(self.catalog[code]["office_fee"]) * float(self.office["fee_multiplier"]), 2)

    # insurance rules

    def frequency_ok(self, pid: str, code: str, tooth: str, d: date) -> bool:
        rule = _parse_freq(self.catalog[code].get("freq_limit", ""))
        if rule is None:
            return True
        count, months, per_tooth = rule
        key = (pid, code, tooth if per_tooth else "")
        past = self.history.get(key, [])
        if months is None:
            return len(past) < count
        since = add_months(d, -months)
        return sum(1 for x in past if x > since) < count

    def benefit(self, pid: str, payer_name: str, rank: str, code: str, allowed: float, d: date) -> tuple[float, str]:
        """What this payer pays on this line, applying deductible, coverage and the annual max."""
        payer = self.payers[payer_name]
        cls = self.catalog[code]["benefit_class"]
        if cls not in COVERED_CLASSES:
            return 0.0, "not covered"
        waiting = int(payer.get("waiting_months_major") or 0)
        start = self.joined.get(pid, date.min)
        if cls == "major" and waiting and start > self.sim_start and d < add_months(start, waiting):
            return 0.0, "waiting period"
        key = (pid, d.year, rank)
        base = allowed
        if cls != "preventive":
            ded = float(payer["deductible"]) - self.deductible_met.get(key, 0.0)
            take = max(0.0, min(ded, base))
            self.deductible_met[key] = self.deductible_met.get(key, 0.0) + take
            base -= take
        pay = base * float(payer[f"coverage_{cls}"])
        room = float(payer["annual_max"]) - self.used.get(key, 0.0)
        note = ""
        if pay > room:
            pay, note = max(0.0, room), "annual max reached"
        self.used[key] = self.used.get(key, 0.0) + pay
        return round(pay, 2), note

    # the run

    def run(self) -> None:
        by_appt: dict[str, list] = {}
        for p in self.sim.procedures:
            by_appt.setdefault(p.appt_id, []).append(p)
        appts = {a.appt_id: a for a in self.sim.appointments}
        for aid in sorted(by_appt, key=lambda x: (appts[x].date, x)):
            self.bill_visit(appts[aid], by_appt[aid])
        self.cancellation_fees()

    def bill_visit(self, appt, procs: list) -> None:
        cfg, rng = self.cfg, self.rng
        pid = appt.patient_id
        patient = self.patients[pid]
        d = date.fromisoformat(appt.date)
        payer = self.payers[patient.payer]
        ptype = payer["payer_type"]
        second = patient.secondary_payer if patient.secondary_payer in self.payers else ""
        g = self.guarantor(pid)
        lines: list[LedgerLine] = []
        for p in procs:
            proc = self.catalog[p.code]
            fee = self.fee(p.code)
            allowed, basis = allowed_amount(proc, payer, self.office, self.medicaid)
            primary = secondary = 0.0
            note = ""
            if ptype == "self_pay":
                allowed_v = allowed or fee
                note = "cash"
            elif allowed is None:  # Medicaid: not covered or prior approval not granted
                allowed_v, note = fee, basis
            elif basis.startswith("elective"):
                allowed_v, note = allowed, "elective"
            elif not self.frequency_ok(pid, p.code, p.tooth, d):
                allowed_v, note = allowed, "frequency limit"
            else:
                allowed_v = allowed
                if ptype == "medicaid":
                    primary = allowed
                else:
                    primary, note = self.benefit(pid, patient.payer, "primary", p.code, allowed, d)
                    if second and patient.age >= 18:
                        normal, _ = self.benefit(pid, second, "secondary", p.code, allowed, d)
                        secondary = round(min(normal, allowed - primary), 2)
                key = (pid, p.code, p.tooth if (_parse_freq(proc.get("freq_limit", "")) or (0, 0, False))[2] else "")
                self.history.setdefault(key, []).append(d)
            patient_part = round(allowed_v - primary - secondary, 2)
            if ptype == "medicaid" and allowed is not None and note == "":
                patient_part = 0.0  # no balance billing on covered Medicaid work
            lines.append(
                LedgerLine(
                    appt.appt_id,
                    pid,
                    g,
                    appt.date,
                    p.code,
                    p.tooth,
                    p.provider_id,
                    patient.payer,
                    fee,
                    round(allowed_v, 2),
                    primary,
                    secondary,
                    patient_part,
                    round(fee - allowed_v, 2),
                    note,
                )
            )
        # claims: one per payer per visit
        for rank, payer_name, field in (
            ("primary", patient.payer, "primary_paid"),
            ("secondary", second, "secondary_paid"),
        ):
            expected = round(sum(getattr(x, field) for x in lines), 2)
            if not payer_name or ptype == "self_pay" or (rank == "secondary" and expected == 0):
                continue
            billed = round(sum(x.fee for x in lines), 2)
            if expected == 0 and all(x.note in ("cash", "elective") for x in lines):
                continue
            kind = "medicaid" if self.payers[payer_name]["payer_type"] == "medicaid" else "commercial"
            lag = cfg["pay_days"][kind]
            paid_on = d + timedelta(days=rng.randint(*lag))
            status, reason, paid = "paid", "", expected
            if rng.random() < cfg["first_pass_denial"][kind]:
                reason = rng.choice(cfg["denial_reasons"])
                if rng.random() < cfg["resubmit_success"]:
                    status = "paid_after_resubmit"
                    paid_on += timedelta(days=rng.randint(*cfg["resubmit_days"]))
                else:
                    status, paid = "written_off", 0.0
                    for x in lines:
                        x.write_off = round(x.write_off + getattr(x, field), 2)
                        setattr(x, field, 0.0)
                        x.note = x.note or "denied"
            if paid_on >= SIM_TODAY and status != "written_off":
                status = "pending"
            cid = f"C{len(self.claims) + 1:07d}"
            self.claims.append(
                Claim(
                    cid,
                    appt.appt_id,
                    pid,
                    payer_name,
                    rank,
                    appt.date,
                    status,
                    paid_on.isoformat() if status != "pending" else "",
                    billed,
                    expected,
                    paid if status != "pending" else 0.0,
                    reason,
                )
            )
            if status in ("paid", "paid_after_resubmit") and paid > 0:
                self.pay(paid_on, g, pid, "insurance", paid, cid)
        self.ledger += lines
        owed = round(sum(x.patient_portion for x in lines), 2)
        if owed > 0:
            self.collect(d, g, pid, owed, appt.appt_id)

    def pay(self, d: date, g: str, pid: str, source: str, amount: float, ref: str) -> None:
        if d < SIM_TODAY and amount > 0:
            self.payments.append(
                Payment(f"Y{len(self.payments) + 1:07d}", d.isoformat(), g, pid, source, round(amount, 2), ref)
            )

    def collect(self, d: date, g: str, pid: str, owed: float, ref: str) -> None:
        """The patient's share: at the desk, later from a statement, financed, on a plan, or never."""
        cfg, rng = self.cfg, self.rng
        t = self.truths.get(g) or self.truths[pid]
        low_income = t.income_tier == "low"
        if owed >= cfg["big_bill"] and rng.random() < cfg["financing_share"] * t.financing_propensity:
            net = owed * (1 - cfg["financing_fee"])
            self.pay(d + timedelta(days=3), g, pid, "financing", net, ref)
            self.ledger.append(
                LedgerLine(
                    ref,
                    pid,
                    g,
                    d.isoformat(),
                    "FIN",
                    "",
                    "",
                    "financing",
                    0,
                    0,
                    0,
                    0,
                    0,
                    round(owed - net, 2),
                    "financing fee",
                )
            )
            return
        if owed >= cfg["big_bill"] and rng.random() < cfg["payment_plan_share"]:
            months = cfg["payment_plan_months"]
            self.plans.add(g)
            for i in range(months):
                when = add_months(d, i + 1)
                part = round(owed / months, 2)
                if when < SIM_TODAY:
                    self.pay(when, g, pid, "payment_plan", part, ref)
                else:
                    self.charges.append((g, d, part, ref))
            return
        if rng.random() < cfg["pay_at_visit_base"] + cfg["pay_at_visit_per_ease"] * (1 - t.price_sensitivity):
            self.pay(d, g, pid, "patient", owed, ref)
            return
        bad = cfg["bad_debt_base"] + cfg["bad_debt_per_sensitivity"] * t.price_sensitivity * (2 if low_income else 1)
        if rng.random() < bad:
            when = d + timedelta(days=cfg["collections_after_days"])
            if when < SIM_TODAY:
                self.ledger.append(
                    LedgerLine(
                        ref,
                        pid,
                        g,
                        when.isoformat(),
                        "BAD",
                        "",
                        "",
                        "collections",
                        0,
                        0,
                        0,
                        0,
                        0,
                        owed,
                        "sent to collections",
                    )
                )
            else:
                self.charges.append((g, d, owed, ref))
            return
        lag = int(
            rng.lognormvariate(0, 0.6)
            * (cfg["statement_days_base"] + cfg["statement_days_per_sensitivity"] * t.price_sensitivity)
        )
        when = d + timedelta(days=max(5, lag))
        if when < SIM_TODAY:
            self.pay(when, g, pid, "patient", owed, ref)
        else:
            self.charges.append((g, d, owed, ref))

    def cancellation_fees(self) -> None:
        policy = self.office.get("cancellation_policy") or {}
        fee = float(policy.get("fee", 0))
        if not fee:
            return
        causes = {x.appt_id: x.cause for x in self.sim.appointment_truth}
        for a in self.sim.appointments:
            if a.status not in ("late_cancel", "no_show") or causes.get(a.appt_id) in self.cfg["fee_waived_for"]:
                continue
            patient = self.patients[a.patient_id]
            if self.payers[patient.payer]["payer_type"] == "medicaid":
                continue
            d = date.fromisoformat(a.status_on)
            g = self.guarantor(a.patient_id)
            collected = self.rng.random() < float(policy.get("collection_rate", 0))
            self.ledger.append(
                LedgerLine(
                    a.appt_id,
                    a.patient_id,
                    g,
                    a.status_on,
                    "CXL",
                    "",
                    "",
                    patient.payer,
                    fee,
                    fee,
                    0,
                    0,
                    fee if collected else 0.0,
                    0.0 if collected else fee,
                    "cancellation fee" if collected else "cancellation fee waived",
                )
            )
            if collected:
                self.pay(
                    d + timedelta(days=self.rng.randint(0, 30)), g, a.patient_id, "cancellation_fee", fee, a.appt_id
                )

    # what is left on SIM_TODAY

    def benefits(self) -> list[Benefit]:
        rows = []
        insured = {
            p.patient_id: p
            for p in self.sim.patients
            if self.payers[p.payer]["payer_type"] in INSURED and p.patient_id in self.sim.current_ids
        }
        years = range(SIM_TODAY.year - 3, SIM_TODAY.year + 1)
        for pid, p in insured.items():
            for y in years:
                for rank, name in (("primary", p.payer), ("secondary", p.secondary_payer)):
                    if not name or name not in self.payers:
                        continue
                    key = (pid, y, rank)
                    if key not in self.used and y != SIM_TODAY.year:
                        continue
                    payer = self.payers[name]
                    mx = float(payer["annual_max"])
                    used = round(self.used.get(key, 0.0), 2)
                    rows.append(
                        Benefit(
                            pid,
                            y,
                            name,
                            rank,
                            mx,
                            used,
                            round(mx - used, 2),
                            float(payer["deductible"]),
                            round(self.deductible_met.get(key, 0.0), 2),
                        )
                    )
        return rows

    def balances(self) -> list[Balance]:
        out: dict[str, Balance] = {}
        for g, d, amount, _ in self.charges:
            b = out.setdefault(g, Balance(g, 0, 0, 0, 0, 0, 0, g in self.plans))
            age = (SIM_TODAY - d).days
            b.balance += amount
            if age <= 30:
                b.current += amount
            elif age <= 60:
                b.days_31_60 += amount
            elif age <= 90:
                b.days_61_90 += amount
            else:
                b.over_90 += amount
        for c in self.claims:
            if c.status == "pending":
                g = self.guarantor(c.patient_id)
                out.setdefault(g, Balance(g, 0, 0, 0, 0, 0, 0, g in self.plans)).insurance_pending += c.expected
        for b in out.values():
            for f in ("balance", "current", "days_31_60", "days_61_90", "over_90", "insurance_pending"):
                setattr(b, f, round(getattr(b, f), 2))
        return sorted(out.values(), key=lambda b: b.guarantor_id)


@dataclass
class Money:
    ledger: list[LedgerLine]
    claims: list[Claim]
    payments: list[Payment]
    benefits: list[Benefit]
    balances: list[Balance]


def run(sim: Sim, configs_dir: Path, seed: int = 42) -> Money:
    biller = Biller(sim, configs_dir, seed)
    biller.run()
    ledger = sorted(biller.ledger, key=lambda x: (x.date, x.appt_id))
    payments = sorted(biller.payments, key=lambda x: (x.date, x.payment_id))
    return Money(ledger, biller.claims, payments, biller.benefits(), biller.balances())


def summary(money: Money, months: int = 12) -> Row:
    """Headline numbers an office manager would check, for the last `months` months."""
    lo = add_months(SIM_TODAY, -months).isoformat()
    today = SIM_TODAY.isoformat()
    lines = [x for x in money.ledger if lo <= x.date < today and x.code not in ("FIN", "BAD")]
    gross = sum(x.fee for x in lines)
    contractual = sum(x.fee - x.allowed for x in lines)
    net = gross - contractual
    collected = sum(p.amount for p in money.payments if lo <= p.date < today)
    claims = [c for c in money.claims if lo <= c.submitted_on < today and c.rank == "primary"]
    denied = sum(c.denial_reason != "" for c in claims)
    paid_days = sorted(
        (date.fromisoformat(c.paid_on) - date.fromisoformat(c.submitted_on)).days
        for c in claims
        if c.status in ("paid", "paid_after_resubmit")
    )
    return {
        "gross": gross,
        "write_off_share": contractual / gross if gross else 0.0,
        "net": net,
        "collected": collected,
        "collection_rate": collected / net if net else 0.0,
        "first_pass_denial": denied / len(claims) if claims else 0.0,
        "median_days_to_pay": paid_days[len(paid_days) // 2] if paid_days else 0,
        "patient_ar": sum(b.balance for b in money.balances),
        "over_90": sum(b.over_90 for b in money.balances),
        "insurance_pending": sum(b.insurance_pending for b in money.balances),
    }


def write(money: Money, out_dir: Path) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    tables: dict[str, list] = {
        "ledger.csv": money.ledger,
        "claims.csv": money.claims,
        "payments.csv": money.payments,
        "benefits.csv": money.benefits,
        "balances.csv": money.balances,
    }
    for name, rows in tables.items():
        with (out_dir / name).open("w", newline="") as f:
            dicts = [asdict(r) for r in rows]
            writer = csv.DictWriter(f, fieldnames=list(dicts[0]))
            writer.writeheader()
            writer.writerows(dicts)
    return out_dir
