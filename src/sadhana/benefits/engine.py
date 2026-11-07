"""Benefits engine.

For one office, from its synced data plus the payer rules in config:
- each patient's benefits today: annual max used and left, deductible met, coverage by class, and when they are
  next eligible for cleanings, exams and x-rays under their plan's frequency limits
- estimates for open treatment: allowed amount, what insurance pays (primary, then secondary) and what the
  patient pays, applied in priority order so the deductible and annual max are used the way a claim would
- year-end phasing: the plan max resets January 1, so non-urgent work that would run past this year's max can
  wait until January and be covered by next year's max instead
- the office's year-end list: insured patients whose open treatment would use benefits that expire Dec 31

Estimates are what the office would tell a patient before treatment; the claim decides the final amount.
"""

from __future__ import annotations

import csv
from dataclasses import asdict, dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

from sqlalchemy import Connection, text

from sadhana.benefits.rules import next_eligible, parse_freq
from sadhana.economics.engine import allowed_amount

Row = dict[str, Any]
PRIORITY = {"urgent": 0, "needed": 1, "elective": 2}
# clinical order on the same tooth: root canal or implant first, then build-up or abutment, then the crown
STEP = {"D3310": 0, "D3320": 0, "D3330": 0, "D6010": 0, "D2950": 1, "D6057": 1, "D2740": 2, "D6065": 2}


def _order(item: Row) -> tuple:
    return (PRIORITY.get(item["priority"], 9), STEP.get(item["code"], 1), item.get("tooth") or "", item["code"])


COVERED = ("preventive", "basic", "major")
INSURED = ("ppo", "premier")
WATCHED_CODES = {
    "D0120": "exam",
    "D1110": "adult cleaning",
    "D1120": "child cleaning",
    "D4910": "perio maintenance",
    "D0274": "bitewing x-rays",
    "D0210": "full x-rays",
}


@dataclass
class Estimate:
    plan_id: str
    code: str
    tooth: str
    priority: str
    fee: float
    allowed: float
    insurance: float
    patient: float
    note: str


@dataclass
class Coverage:
    """What is left on one plan for one year; changes as estimates are applied."""

    payer: str
    annual_max: float
    remaining: float
    deductible: float
    deductible_left: float
    coverage: dict[str, float] = field(default_factory=dict)


class BenefitsEngine:
    def __init__(self, office: Row, configs_dir: Path, today: date) -> None:
        self.office = office
        self.today = today
        state = office["state"]
        with (configs_dir / "catalog" / "service_catalog.csv").open(newline="") as f:
            self.catalog = {r["code"]: r for r in csv.DictReader(f)}
        with (configs_dir / "payers" / f"payers_{state}.csv").open(newline="") as f:
            self.payers = {r["payer"]: r for r in csv.DictReader(f)}
        with (configs_dir / "payers" / f"medicaid_fees_{state}.csv").open(newline="") as f:
            self.medicaid = {r["code"]: r for r in csv.DictReader(f)}

    # rules

    def fee(self, code: str) -> float:
        return round(float(self.catalog[code]["office_fee"]) * float(self.office["fee_multiplier"]), 2)

    def plan(self, payer: str, year_row: Row | None) -> Coverage | None:
        p = self.payers.get(payer)
        if not p or p["payer_type"] not in INSURED:
            return None
        mx, ded = float(p["annual_max"]), float(p["deductible"])
        used = float(year_row["used"]) if year_row else 0.0
        met = float(year_row["deductible_met"]) if year_row else 0.0
        cov = {c: float(p[f"coverage_{c}"]) for c in COVERED}
        return Coverage(payer, mx, round(mx - used, 2), ded, round(max(0.0, ded - met), 2), cov)

    def fresh(self, cov: Coverage) -> Coverage:
        """The same plan on January 1: full max, deductible not yet met."""
        return Coverage(cov.payer, cov.annual_max, cov.annual_max, cov.deductible, cov.deductible, cov.coverage)

    def _plan_pays(self, cov: Coverage, cls: str, allowed: float) -> tuple[float, bool]:
        """What this plan pays on one line, using up its deductible and max; True if the max cut it short."""
        if cls not in COVERED:
            return 0.0, False
        base = allowed
        take = 0.0
        if cls != "preventive":
            take = min(cov.deductible_left, base)
            base -= take
        full = base * cov.coverage[cls]
        pays = min(full, max(0.0, cov.remaining))
        cov.deductible_left = round(cov.deductible_left - take, 2)
        cov.remaining = round(cov.remaining - pays, 2)
        return round(pays, 2), pays < full - 0.005

    def estimate(
        self,
        payer: str,
        items: list[Row],
        primary: Coverage | None,
        secondary: Coverage | None = None,
        not_before: dict[str, date] | None = None,
    ) -> list[Estimate]:
        """Estimates in priority order; `primary` and `secondary` are used up as items are applied."""
        p = self.payers[payer]
        ptype = p["payer_type"]
        out = []
        for it in sorted(items, key=_order):
            code = it["code"]
            proc = self.catalog[code]
            fee = self.fee(code)
            allowed, basis = allowed_amount(proc, p, self.office, self.medicaid)
            ins, note = 0.0, ""
            if allowed is None:
                allowed, note = fee, f"Medicaid {basis}"
            elif ptype == "self_pay":
                note = "cash price"
            elif ptype == "medicaid":
                ins = allowed
            elif basis.startswith("elective"):
                note = "elective, not covered"
            elif primary is not None:
                wait = (not_before or {}).get(code)
                if wait and wait > self.today:
                    note = f"plan covers again from {wait.isoformat()}"
                else:
                    cls = proc["benefit_class"]
                    ins, capped = self._plan_pays(primary, cls, allowed)
                    if secondary is not None and allowed - ins > 0:
                        extra, _ = self._plan_pays(secondary, cls, allowed)
                        ins += min(extra, allowed - ins)
                    if capped:
                        note = "annual max reached"
            ins = round(min(ins, allowed), 2)
            out.append(
                Estimate(
                    it.get("plan_id", ""),
                    code,
                    it.get("tooth") or "",
                    it["priority"],
                    fee,
                    round(allowed, 2),
                    ins,
                    round(allowed - ins, 2),
                    note,
                )
            )
        return out

    def phase(self, payer: str, items: list[Row], primary: Coverage) -> Row:
        """Do it all now, or finish this year's max now and move non-urgent work to January?"""
        now_all = self.estimate(payer, items, self._copy(primary))
        this_cov, next_cov = self._copy(primary), self.fresh(primary)
        this_year, next_year = [], []
        for it in sorted(items, key=_order):
            trial = self._copy(this_cov)
            est = self.estimate(payer, [it], trial)[0]
            capped = est.note == "annual max reached"
            if capped and it["priority"] != "urgent":
                next_year.append(it)
                self.estimate(payer, [it], next_cov)
            else:
                this_year.append(it)
                this_cov = trial
        later = self.estimate(payer, next_year, self.fresh(primary))
        now = self.estimate(payer, this_year, self._copy(primary))
        patient_now_all = round(sum(e.patient for e in now_all), 2)
        patient_phased = round(sum(e.patient for e in now + later), 2)
        return {
            "do_now": [asdict(e) for e in now],
            "move_to_january": [asdict(e) for e in later],
            "patient_pays_if_all_now": patient_now_all,
            "patient_pays_if_phased": patient_phased,
            "patient_saves": round(patient_now_all - patient_phased, 2),
            "insurance_this_year": round(sum(e.insurance for e in now), 2),
            "insurance_next_year": round(sum(e.insurance for e in later), 2),
        }

    @staticmethod
    def _copy(c: Coverage) -> Coverage:
        return Coverage(c.payer, c.annual_max, c.remaining, c.deductible, c.deductible_left, dict(c.coverage))

    # data

    def _benefit_rows(self, conn: Connection, pid: str | None = None) -> dict[tuple[str, str], Row]:
        sql = "SELECT * FROM benefits WHERE year = :y" + (" AND patient_id = :p" if pid else "")
        rows = conn.execute(text(sql), {"y": self.today.year, "p": pid}).mappings()
        return {(r["patient_id"], r["rank"]): dict(r) for r in rows}

    def _open_items(self, conn: Connection, pid: str | None = None) -> dict[str, list[Row]]:
        sql = (
            "SELECT plan_id, patient_id, code, tooth, priority, status FROM treatment_plan "
            "WHERE status IN ('proposed', 'accepted')" + (" AND patient_id = :p" if pid else "")
        )
        out: dict[str, list[Row]] = {}
        for r in conn.execute(text(sql), {"p": pid}).mappings():
            out.setdefault(r["patient_id"], []).append(dict(r))
        return out

    def patient(self, conn: Connection, pid: str) -> Row | None:
        """Everything the front desk needs to talk money with one patient."""
        p = conn.execute(text("SELECT * FROM patients WHERE patient_id = :p"), {"p": pid}).mappings().first()
        if p is None:
            return None
        rows = self._benefit_rows(conn, pid)
        primary = self.plan(p["payer"], rows.get((pid, "primary")))
        secondary = self.plan(p["secondary_payer"] or "", rows.get((pid, "secondary"))) if primary else None
        covered = conn.execute(
            text("SELECT code, date FROM ledger WHERE patient_id = :p AND primary_paid > 0 AND code = ANY(:codes)"),
            {"p": pid, "codes": list(WATCHED_CODES)},
        ).all()
        history: dict[str, list[date]] = {}
        for code, d in covered:
            history.setdefault(code, []).append(d)
        eligible = {}
        age = (self.today - p["birth_date"]).days // 365 if p["birth_date"] else 30
        maint = conn.execute(text("SELECT maintenance FROM perio WHERE patient_id = :p"), {"p": pid}).scalar()
        skip = {"D1110" if age < 14 else "D1120"} | (set() if maint else {"D4910"})
        if primary:
            for code, label in WATCHED_CODES.items():
                if code in skip:
                    continue
                rule = parse_freq(self.catalog[code].get("freq_limit", ""))
                when = next_eligible(rule, history.get(code, []), self.today)
                eligible[label] = when.isoformat() if when else "not covered again"
        items = self._open_items(conn, pid).get(pid, [])
        result: Row = {
            "patient_id": pid,
            "payer": p["payer"],
            "payer_type": self.payers.get(p["payer"], {}).get("payer_type", "unknown"),
            "secondary_payer": p["secondary_payer"] or None,
            "as_of": self.today.isoformat(),
            "plan": asdict(primary) if primary else None,
            "next_eligible": eligible,
            "open_treatment": [
                asdict(e)
                for e in self.estimate(
                    p["payer"],
                    items,
                    self._copy(primary) if primary else None,
                    self._copy(secondary) if secondary else None,
                )
            ],
        }
        if primary and items:
            result["year_end_options"] = self.phase(p["payer"], items, primary)
        return result

    def year_end(self, conn: Connection, limit: int | None = None) -> list[Row]:
        """Active insured patients whose open treatment would use benefits that expire Dec 31, most at stake first."""
        patients = conn.execute(
            text(
                "SELECT p.patient_id, p.payer, p.language, p.sms_consent FROM patients p "
                "JOIN patient_status s USING (patient_id) WHERE s.status = 'active'"
            )
        ).mappings()
        rows = self._benefit_rows(conn)
        items = self._open_items(conn)
        days_left = (date(self.today.year, 12, 31) - self.today).days
        out = []
        for p in patients:
            pid = p["patient_id"]
            primary = self.plan(p["payer"], rows.get((pid, "primary")))
            if primary is None or pid not in items or primary.remaining <= 0:
                continue
            ph = self.phase(p["payer"], items[pid], primary)
            if ph["insurance_this_year"] <= 0:
                continue
            out.append(
                {
                    "patient_id": pid,
                    "payer": p["payer"],
                    "language": p["language"],
                    "sms_consent": p["sms_consent"],
                    "benefits_left": primary.remaining,
                    "open_items": len(items[pid]),
                    "open_value": round(sum(self.fee(i["code"]) for i in items[pid]), 2),
                    "insurance_if_done_this_year": ph["insurance_this_year"],
                    "patient_pays_this_year": round(sum(e["patient"] for e in ph["do_now"]), 2),
                    "items_better_in_january": len(ph["move_to_january"]),
                    "patient_saves_by_phasing": ph["patient_saves"],
                    "days_left": days_left,
                }
            )
        out.sort(key=lambda r: (-r["insurance_if_done_this_year"], r["patient_id"]))
        return out[:limit] if limit else out
