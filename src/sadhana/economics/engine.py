"""Service economics engine.

For one office config it works out:
- who can perform each procedure (state scope rules, certs, dentist tier, specialty, equipment)
- what each payer pays for each procedure
- direct costs: lab, materials, labor by comp model
- net margin, margin per chair hour, margin per doctor hour
- expected late-cancel fee value by payer

Cash margin = collected - lab - materials - provider labor - support labor.
Every dentist can carry an hourly value (production per hour on other work). Ranking subtracts it,
so routine work goes to the cheapest qualified person and the owner's chair stays on big cases.
"""

from __future__ import annotations

import csv
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

Row = dict[str, Any]

DENTIST_ROLES = {"owner_dentist", "associate_dentist", "specialist"}
# Every licensed dentist holds these; other certs are competency certs tracked per dentist.
DENTIST_IMPLICIT_CERTS = {"local_anesthesia", "nitrous", "xray"}
BENEFIT_TO_PCT = {
    "preventive": "preventive",
    "basic": "basic",
    "major": "major",
    "ortho": "major",
    "none": "major",
}


@dataclass(frozen=True)
class OfficeInputs:
    office: Row
    scope: Row
    catalog: list[Row]
    payers: dict[str, Row]
    medicaid: dict[str, Row]


@dataclass(frozen=True)
class EconomicsResult:
    office_id: str
    eligibility: list[Row]
    detail: list[Row]
    not_covered: list[Row]
    summary: list[Row]
    payers: list[Row]


def _load_csv(path: Path) -> list[Row]:
    with path.open(newline="") as f:
        return list(csv.DictReader(f))


def load_inputs(office_path: Path, configs_dir: Path) -> OfficeInputs:
    office: Row = json.loads(office_path.read_text())
    state = office["state"]
    scope: Row = json.loads((configs_dir / "rules" / f"scope_{state}.json").read_text())
    catalog = _load_csv(configs_dir / "catalog" / "service_catalog.csv")
    payers = {p["payer"]: p for p in _load_csv(configs_dir / "payers" / f"payers_{state}.csv")}
    medicaid = {m["code"]: m for m in _load_csv(configs_dir / "payers" / f"medicaid_fees_{state}.csv")}
    return OfficeInputs(office, scope, catalog, payers, medicaid)


def staff_certs(staff: Row) -> set[str]:
    certs = set(staff.get("certs", []))
    if staff["role"] in DENTIST_ROLES:
        certs |= DENTIST_IMPLICIT_CERTS
    return certs


def can_perform(staff: Row, proc: Row, office: Row) -> tuple[bool, str]:
    """Whether this staff member can be the primary provider, and why not if they can't."""
    roles = proc["primary_roles"].split(";")
    role = staff["role"]
    is_dentist = role in DENTIST_ROLES
    if role == "front_desk":
        return False, "front desk"
    if not ((is_dentist and "dentist" in roles) or role in roles):
        return False, "role not allowed"
    if role == "specialist" and proc["code"] not in staff.get("scope_codes", []):
        return False, "outside specialist scope"
    need = proc["required_cert"]
    if need and need not in staff_certs(staff):
        return False, f"missing cert {need}"
    if is_dentist and proc["min_dentist_tier"] == "senior" and staff.get("tier") != "senior":
        return False, "requires senior dentist"
    equipment = proc["equipment"]
    if equipment and equipment not in office.get("equipment", []):
        return False, f"office lacks {equipment}"
    return True, ""


def can_support(staff: Row, proc: Row) -> bool:
    if proc["support_role"] == "none":
        return False
    return bool(staff["role"] in proc["support_role"].split(";"))


def supervision_note(staff: Row, proc: Row) -> str:
    if staff["role"] == "hygienist" and proc["required_cert"] in ("local_anesthesia", "nitrous"):
        return "dentist must be in office"
    return ""


def allowed_amount(proc: Row, payer: Row, office: Row, medicaid: dict[str, Row]) -> tuple[float | None, str]:
    """What this payer allows for this procedure at this office, and the basis for it."""
    fee = float(proc["office_fee"]) * office["fee_multiplier"]
    ptype = payer["payer_type"]
    if ptype == "self_pay":
        return round(fee * (1 - office["cash_discount"]), 2), "cash"
    if ptype == "medicaid":
        m = medicaid.get(proc["code"])
        if not m or m["covered"] == "0":
            return None, "not covered"
        if float(m["fee"]) == 0:
            return None, "by report / prior approval"
        return float(m["fee"]), str(m["source"])
    if proc["elective_cash"] == "1":
        # Elective cosmetic work: insured patients usually pay the office fee themselves.
        return round(fee * (1 - office["cash_discount"]), 2), "elective, patient pays"
    pct = float(payer[f"allowed_pct_{BENEFIT_TO_PCT[proc['benefit_class']]}"])
    return round(fee * pct, 2), "ppo fee schedule"


def labor_cost(staff: Row, hours: float, collected: float) -> tuple[float, float]:
    """(cash cost, opportunity cost). Opportunity cost decides who does the work; it is never
    subtracted from the office's cash margin."""
    comp = staff["comp"]
    opp = float(comp.get("hourly_value", 0)) * hours
    if comp["type"] == "hourly":
        return float(comp["rate"]) * hours, opp
    if comp["type"] == "pct_collections":
        return float(comp["rate"]) * collected, opp
    if comp["type"] == "owner_opportunity":
        return 0.0, opp
    raise ValueError(f"unknown comp type {comp['type']}")


def build(inputs: OfficeInputs) -> EconomicsResult:
    office, scope = inputs.office, inputs.scope
    staff = [s for s in office["staff"] if s["role"] != "front_desk"]
    mix: dict[str, float] = office["payer_mix"]

    eligibility: list[Row] = []
    for proc in inputs.catalog:
        for s in staff:
            ok, why = can_perform(s, proc, office)
            eligibility.append(
                {
                    "code": proc["code"],
                    "name": proc["name"],
                    "staff_id": s["id"],
                    "staff": s["name"],
                    "role": s["role"],
                    "eligible": int(ok),
                    "reason": why,
                    "supervision": supervision_note(s, proc) if ok else "",
                }
            )

    detail: list[Row] = []
    not_covered: list[Row] = []
    summary: list[Row] = []
    for proc in inputs.catalog:
        providers = [s for s in staff if can_perform(s, proc, office)[0]]
        supports = [s for s in staff if can_support(s, proc)]
        support = min(supports, key=lambda s: s["comp"].get("rate", 0)) if supports else None
        lab = float(proc["lab_cost"])
        mat = float(proc["materials_cost"])
        office_fee = float(proc["office_fee"]) * office["fee_multiplier"]
        best: tuple[Row, float, float, float] | None = None
        per_provider: dict[str, float | None] = {}

        for s in providers:
            is_dentist = s["role"] in DENTIST_ROLES
            chair_hrs = float(proc["chair_min"]) * s["speed"] / 60
            doc_hrs = float(proc["doctor_min"]) * s["speed"] / 60 if is_dentist else 0.0
            prov_hrs = doc_hrs if is_dentist and doc_hrs > 0 else chair_hrs
            w_margin = w_econ = w_share = 0.0
            for pname, share in mix.items():
                allowed, basis = allowed_amount(proc, inputs.payers[pname], office, inputs.medicaid)
                if allowed is None:
                    not_covered.append(
                        {
                            "code": proc["code"],
                            "name": proc["name"],
                            "payer": pname,
                            "provider_id": s["id"],
                            "status": basis,
                        }
                    )
                    continue
                collected = allowed * office["collection_rate"]
                p_cost, p_opp = labor_cost(s, prov_hrs, collected)
                sup_cost = (
                    float(support["comp"]["rate"]) * chair_hrs
                    if support and support["comp"]["type"] == "hourly"
                    else 0.0
                )
                margin = collected - lab - mat - p_cost - sup_cost
                writeoff = max(office_fee - allowed, 0.0)
                detail.append(
                    {
                        "code": proc["code"],
                        "name": proc["name"],
                        "payer": pname,
                        "provider_id": s["id"],
                        "provider_role": s["role"],
                        "provider_tier": s.get("tier", ""),
                        "basis": basis,
                        "office_fee": round(office_fee, 2),
                        "allowed": round(allowed, 2),
                        "writeoff": round(writeoff, 2),
                        "writeoff_pct": round(writeoff / office_fee, 3) if office_fee else 0.0,
                        "collected": round(collected, 2),
                        "lab": lab,
                        "materials": mat,
                        "provider_labor": round(p_cost, 2),
                        "support_id": support["id"] if support else "",
                        "support_labor": round(sup_cost, 2),
                        "net_margin": round(margin, 2),
                        "chair_hours": round(chair_hrs, 2),
                        "doctor_hours": round(doc_hrs, 2),
                        "margin_per_chair_hour": (round(margin / chair_hrs, 2) if chair_hrs else None),
                        "margin_per_doctor_hour": round(margin / doc_hrs, 2) if doc_hrs else None,
                        "opportunity_cost": round(p_opp, 2),
                        "economic_margin": round(margin - p_opp, 2),
                        "visits": int(proc["visits"]),
                        "lab_days": int(proc["lab_days"]),
                        "supervision": supervision_note(s, proc),
                    }
                )
                w_margin += margin * share
                w_econ += (margin - p_opp) * share
                w_share += share
            if not w_share:
                continue
            exp_margin = w_margin / w_share
            exp_econ = w_econ / w_share
            per_hr = exp_margin / chair_hrs if chair_hrs else None
            rank_key = exp_econ / chair_hrs if chair_hrs else exp_econ
            per_provider[s["id"]] = round(per_hr, 2) if per_hr is not None else None
            if best is None or rank_key > best[3]:
                best = (s, per_hr if per_hr is not None else exp_margin, exp_margin, rank_key)

        summary.append(
            {
                "code": proc["code"],
                "name": proc["name"],
                "category": proc["category"],
                "eligible_providers": ";".join(s["id"] for s in providers) or "NONE",
                "best_provider": best[0]["id"] if best else "",
                "expected_margin": round(best[2], 2) if best else None,
                "expected_margin_per_chair_hour": (round(best[1], 2) if best and float(proc["chair_min"]) else None),
                "providers_margin_per_chair_hour": per_provider,
                "chair_min": int(proc["chair_min"]),
                "visits": int(proc["visits"]),
            }
        )

    exempt = set(scope.get("cancellation_fee_exempt_payer_types", []))
    policy = office["cancellation_policy"]
    payer_rows: list[Row] = []
    for pname, share in mix.items():
        payer = inputs.payers[pname]
        rows = [r for r in detail if r["payer"] == pname]
        avg_wo = sum(r["writeoff_pct"] for r in rows) / len(rows) if rows else 0.0
        chargeable = payer["payer_type"] not in exempt
        payer_rows.append(
            {
                "payer": pname,
                "share": share,
                "avg_writeoff_pct": round(avg_wo, 3),
                "can_charge_cancel_fee": chargeable,
                "expected_late_cancel_fee": (
                    round(policy["fee"] * policy["collection_rate"], 2) if chargeable else 0.0
                ),
            }
        )

    return EconomicsResult(office["office_id"], eligibility, detail, not_covered, summary, payer_rows)


def _write_csv(path: Path, rows: list[Row]) -> None:
    keys: list[str] = []
    for r in rows:
        keys.extend(k for k in r if k not in keys)
    with path.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=keys)
        writer.writeheader()
        writer.writerows(rows)


def write_outputs(result: EconomicsResult, outputs_dir: Path) -> Path:
    out = outputs_dir / result.office_id
    out.mkdir(parents=True, exist_ok=True)
    _write_csv(out / "eligibility_matrix.csv", result.eligibility)
    _write_csv(out / "economics_detail.csv", result.detail)
    _write_csv(out / "not_covered.csv", result.not_covered)
    _write_csv(out / "procedure_summary.csv", result.summary)
    _write_csv(out / "payer_summary.csv", result.payers)
    return out
