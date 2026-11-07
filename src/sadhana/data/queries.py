"""The first questions the product answers from the database, for one office at a time.

Every function takes a connection from `office_session`, so row-level security keeps it inside one office.
`today` is passed in: the real date for a live office, the simulator's fixed date for a simulated one.
"""

from __future__ import annotations

import csv
from collections import defaultdict
from datetime import date
from pathlib import Path
from typing import Any

from sqlalchemy import Connection, text

from sadhana.benefits.rules import add_months

Row = dict[str, Any]
OPEN = ("proposed", "accepted")


def office_fees(office: Row, configs_dir: Path) -> dict[str, float]:
    """The office's full fee per procedure code: catalog fee times the office's fee multiplier."""
    with (configs_dir / "catalog" / "service_catalog.csv").open(newline="") as f:
        return {
            r["code"]: round(float(r["office_fee"]) * float(office.get("fee_multiplier", 1.0)), 2)
            for r in csv.DictReader(f)
        }


OVERDUE_SQL = """
SELECT p.patient_id, s.last_cleaning, p.payer, p.language, p.sms_consent, p.phone_type, p.guardian_id,
       coalesce(pe.maintenance, false) AS perio_maintenance,
       (:today - s.last_cleaning) AS days_since_cleaning,
       (:today - s.last_cleaning) - CASE WHEN coalesce(pe.maintenance, false) THEN 91 ELSE 182 END AS days_overdue
FROM patient_status s
JOIN patients p USING (patient_id)
LEFT JOIN perio pe USING (patient_id)
WHERE s.status = 'active'
  AND s.next_cleaning IS NULL
  AND s.last_cleaning IS NOT NULL
  AND s.last_cleaning < (CAST(:today AS date)
      - make_interval(months => CASE WHEN coalesce(pe.maintenance, false) THEN 4 ELSE 7 END))
ORDER BY s.last_cleaning, p.patient_id
"""


def overdue(conn: Connection, today: date, limit: int | None = None) -> list[Row]:
    """Active patients past due for a cleaning (6 months, or 3 for gum maintenance, plus a month of grace)
    with nothing on the books; longest overdue first."""
    sql = OVERDUE_SQL + (" LIMIT :limit" if limit else "")
    rows = conn.execute(text(sql), {"today": today, "limit": limit}).mappings()
    return [dict(r) for r in rows]


def unscheduled_treatment(conn: Connection, fees: dict[str, float], limit: int | None = None) -> list[Row]:
    """Patients with diagnosed work that is proposed or accepted but not booked, largest value first."""
    rows = conn.execute(
        text(
            """
            SELECT t.patient_id, t.code, t.status, t.priority, t.diagnosed_on, p.payer, s.status AS patient_status
            FROM treatment_plan t
            JOIN patients p USING (patient_id)
            LEFT JOIN patient_status s USING (patient_id)
            WHERE t.status IN ('proposed', 'accepted') AND coalesce(s.status, '') <> 'archived'
            """
        )
    ).mappings()
    by_patient: dict[str, Row] = {}
    for r in rows:
        p = by_patient.setdefault(
            r["patient_id"],
            {
                "patient_id": r["patient_id"],
                "payer": r["payer"],
                "patient_status": r["patient_status"],
                "items": 0,
                "accepted_items": 0,
                "urgent_items": 0,
                "value": 0.0,
                "oldest_diagnosis": r["diagnosed_on"],
                "codes": [],
            },
        )
        p["items"] += 1
        p["accepted_items"] += r["status"] == "accepted"
        p["urgent_items"] += r["priority"] == "urgent"
        p["value"] = round(p["value"] + fees.get(r["code"], 0.0), 2)
        p["codes"].append(r["code"])
        if r["diagnosed_on"] and (p["oldest_diagnosis"] is None or r["diagnosed_on"] < p["oldest_diagnosis"]):
            p["oldest_diagnosis"] = r["diagnosed_on"]
    out = sorted(by_patient.values(), key=lambda p: (-p["value"], p["patient_id"]))
    return out[:limit] if limit else out


def unused_benefits(conn: Connection, today: date, fees: dict[str, float], limit: int | None = None) -> list[Row]:
    """Active insured patients with this year's benefits left, next to the open treatment those benefits could
    help pay for. Ranked by the smaller of the two: money that expires December 31 if nothing is booked."""
    rows = conn.execute(
        text(
            """
            SELECT b.patient_id, b.payer, b.annual_max, b.used, b.remaining
            FROM benefits b
            JOIN patient_status s USING (patient_id)
            WHERE b.year = :year AND b.rank = 'primary' AND s.status = 'active' AND b.remaining > 0
            """
        ),
        {"year": today.year},
    ).mappings()
    open_value: dict[str, float] = defaultdict(float)
    for r in unscheduled_treatment(conn, fees):
        open_value[r["patient_id"]] = r["value"]
    out = []
    for r in rows:
        remaining = float(r["remaining"])
        open_v = open_value.get(r["patient_id"], 0.0)
        out.append(
            {
                "patient_id": r["patient_id"],
                "payer": r["payer"],
                "annual_max": float(r["annual_max"]),
                "used": float(r["used"]),
                "remaining": remaining,
                "open_treatment_value": open_v,
                "at_stake": round(min(remaining, open_v), 2),
            }
        )
    out.sort(key=lambda r: (-r["at_stake"], -r["remaining"], r["patient_id"]))
    return out[:limit] if limit else out


def summary(conn: Connection, today: date, fees: dict[str, float]) -> Row:
    counts = conn.execute(
        text(
            """
            SELECT count(*) AS on_file,
                   count(*) FILTER (WHERE status = 'active') AS active,
                   count(*) FILTER (WHERE status = 'inactive') AS inactive
            FROM patient_status
            """
        )
    ).one()
    open_rows = unscheduled_treatment(conn, fees)
    benefits = unused_benefits(conn, today, fees)
    upcoming = conn.execute(
        text("SELECT count(*) FROM appointments WHERE status = 'scheduled' AND date >= :today"), {"today": today}
    ).scalar()
    return {
        "as_of": today.isoformat(),
        "patients_on_file": counts.on_file,
        "active_patients": counts.active,
        "inactive_patients": counts.inactive,
        "overdue_for_cleaning": len(overdue(conn, today)),
        "appointments_booked_ahead": upcoming,
        "unscheduled_treatment_patients": len(open_rows),
        "unscheduled_treatment_value": round(sum(r["value"] for r in open_rows), 2),
        "unused_benefits_patients": len(benefits),
        "unused_benefits_value": round(sum(r["remaining"] for r in benefits), 2),
        "benefits_at_stake_value": round(sum(r["at_stake"] for r in benefits), 2),
    }


PAYER_SQL = """
WITH lines AS (
    SELECT payer, patient_id, appt_id, fee, allowed, primary_paid, secondary_paid, patient_portion, write_off
    FROM ledger
    WHERE date >= :since AND date < :today AND code NOT IN ('FIN', 'BAD', 'CXL')
),
claim_stats AS (
    SELECT payer,
           count(*) AS claims,
           count(*) FILTER (WHERE denial_reason IS NOT NULL AND denial_reason <> '') AS denied,
           percentile_cont(0.5) WITHIN GROUP (ORDER BY paid_on - submitted_on)
               FILTER (WHERE paid_on IS NOT NULL) AS median_days_to_pay,
           coalesce(sum(expected) FILTER (WHERE status = 'pending'), 0) AS pending
    FROM claims
    WHERE rank = 'primary' AND submitted_on >= :since AND submitted_on < :today
    GROUP BY payer
)
SELECT l.payer,
       count(DISTINCT l.patient_id) AS patients,
       count(DISTINCT l.appt_id) AS visits,
       sum(l.fee) AS gross,
       sum(l.allowed) AS allowed,
       sum(l.primary_paid + l.secondary_paid) AS insurance_paid,
       sum(l.patient_portion) AS patient_portion,
       sum(l.fee - l.allowed) AS contractual_write_off,
       c.claims, c.denied, c.median_days_to_pay, c.pending
FROM lines l
LEFT JOIN claim_stats c USING (payer)
GROUP BY l.payer, c.claims, c.denied, c.median_days_to_pay, c.pending
ORDER BY gross DESC
"""


def payer_scorecard(conn: Connection, today: date, months: int = 12) -> list[Row]:
    """How each payer treats this office over the last `months` months: volume, what it allows against the
    office's fee, write-offs, denials and how fast it pays. The basis for fee negotiations and payer mix."""
    since = add_months(today, -months)
    out = []
    for r in conn.execute(text(PAYER_SQL), {"since": since, "today": today}).mappings():
        gross = float(r["gross"] or 0)
        claims = r["claims"] or 0
        out.append(
            {
                "payer": r["payer"],
                "patients": r["patients"],
                "visits": r["visits"],
                "gross": round(gross, 2),
                "allowed_share_of_fee": round(float(r["allowed"] or 0) / gross, 3) if gross else 0.0,
                "write_off_share": round(float(r["contractual_write_off"] or 0) / gross, 3) if gross else 0.0,
                "insurance_paid": round(float(r["insurance_paid"] or 0), 2),
                "patient_portion": round(float(r["patient_portion"] or 0), 2),
                "claims": claims,
                "denial_rate": round((r["denied"] or 0) / claims, 3) if claims else 0.0,
                "median_days_to_pay": float(r["median_days_to_pay"]) if r["median_days_to_pay"] is not None else None,
                "insurance_pending": round(float(r["pending"] or 0), 2),
            }
        )
    return out
