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
