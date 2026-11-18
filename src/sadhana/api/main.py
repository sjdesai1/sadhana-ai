"""FastAPI app. One app holds every layer as modules; this file wires the routes."""

from datetime import date, timedelta
from functools import lru_cache
from typing import Any

from fastapi import FastAPI, HTTPException, Query
from sqlalchemy import Engine, text
from sqlalchemy.exc import OperationalError

from sadhana import __version__
from sadhana.benefits.engine import BenefitsEngine
from sadhana.config import settings
from sadhana.data import queries
from sadhana.data.db import get_engine, office_session
from sadhana.economics.engine import build, load_inputs
from sadhana.simulator.population import SIM_TODAY

app = FastAPI(title="Sadhana AI", version=__version__)
SIMULATED_TODAY = SIM_TODAY  # simulated offices live on the simulator's fixed date


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "version": __version__, "env": settings.env}


@app.get("/offices")
def list_offices() -> list[str]:
    return sorted(p.stem for p in (settings.configs_dir / "offices").glob("*.json"))


@app.get("/offices/{office_id}/economics")
def office_economics(office_id: str) -> dict[str, Any]:
    path = settings.configs_dir / "offices" / f"{office_id}.json"
    if not path.exists():
        raise HTTPException(status_code=404, detail=f"unknown office {office_id}")
    result = build(load_inputs(path, settings.configs_dir))
    return {"office_id": result.office_id, "procedures": result.summary, "payers": result.payers}


# ---- from the database (make sync first) ----


@lru_cache
def engine() -> Engine:
    return get_engine()


def _synced_office(office_id: str) -> tuple[dict[str, Any], date]:
    """The office's config and its 'today'; 404 if it was never synced, 503 if the database is down."""
    if not office_id.replace("_", "").isalnum() or office_id != office_id.lower():
        raise HTTPException(status_code=404, detail=f"unknown office {office_id}")
    try:
        with engine().connect() as conn:
            row = conn.execute(
                text("SELECT config, source FROM sadhana_meta.offices WHERE office_id = :o"), {"o": office_id}
            ).first()
    except OperationalError as exc:
        raise HTTPException(status_code=503, detail="database unavailable") from exc
    if row is None:
        raise HTTPException(status_code=404, detail=f"office {office_id} has no synced data; run make sync")
    today = SIMULATED_TODAY if row.source == "simulator" else date.today()
    return row.config, today


@app.get("/offices/{office_id}/summary")
def office_summary(office_id: str) -> dict[str, Any]:
    office, today = _synced_office(office_id)
    fees = queries.office_fees(office, settings.configs_dir)
    with office_session(engine(), office_id) as conn:
        return {"office_id": office_id, **queries.summary(conn, today, fees)}


@app.get("/offices/{office_id}/overdue")
def office_overdue(office_id: str, limit: int = Query(50, ge=1, le=1000)) -> list[dict[str, Any]]:
    _, today = _synced_office(office_id)
    with office_session(engine(), office_id) as conn:
        return queries.overdue(conn, today, limit)


@app.get("/offices/{office_id}/unscheduled-treatment")
def office_unscheduled(office_id: str, limit: int = Query(50, ge=1, le=1000)) -> list[dict[str, Any]]:
    office, _ = _synced_office(office_id)
    fees = queries.office_fees(office, settings.configs_dir)
    with office_session(engine(), office_id) as conn:
        return queries.unscheduled_treatment(conn, fees, limit)


@app.get("/offices/{office_id}/unused-benefits")
def office_unused_benefits(office_id: str, limit: int = Query(50, ge=1, le=1000)) -> list[dict[str, Any]]:
    office, today = _synced_office(office_id)
    fees = queries.office_fees(office, settings.configs_dir)
    with office_session(engine(), office_id) as conn:
        return queries.unused_benefits(conn, today, fees, limit)


@app.get("/offices/{office_id}/payers")
def office_payers(office_id: str, months: int = Query(12, ge=1, le=36)) -> list[dict[str, Any]]:
    _, today = _synced_office(office_id)
    with office_session(engine(), office_id) as conn:
        return queries.payer_scorecard(conn, today, months)


@app.get("/offices/{office_id}/year-end")
def office_year_end(office_id: str, limit: int = Query(50, ge=1, le=1000)) -> list[dict[str, Any]]:
    office, today = _synced_office(office_id)
    benefits = BenefitsEngine(office, settings.configs_dir, today)
    with office_session(engine(), office_id) as conn:
        return benefits.year_end(conn, limit)


@app.get("/offices/{office_id}/patients/{patient_id}/benefits")
def patient_benefits(office_id: str, patient_id: str) -> dict[str, Any]:
    office, today = _synced_office(office_id)
    benefits = BenefitsEngine(office, settings.configs_dir, today)
    with office_session(engine(), office_id) as conn:
        result = benefits.patient(conn, patient_id)
    if result is None:
        raise HTTPException(status_code=404, detail=f"unknown patient {patient_id}")
    return result


@app.get("/offices/{office_id}/next-best")
def office_next_best(office_id: str, limit: int = Query(50, ge=1, le=1000)) -> list[dict[str, Any]]:
    """Each patient's most valuable open item to raise, ranked by expected value (make models first)."""
    _synced_office(office_id)
    with office_session(engine(), office_id) as conn:
        rows = conn.execute(
            text(
                "SELECT patient_id, plan_id, code, tooth, p_book, value, expected_value, benefits_this_year, reason "
                "FROM next_best ORDER BY expected_value DESC LIMIT :n"
            ),
            {"n": limit},
        ).mappings()
        return [
            {
                k: (float(v) if k in ("p_book", "value", "expected_value", "benefits_this_year") else v)
                for k, v in r.items()
            }
            for r in rows
        ]


def _plain(row: Any) -> dict[str, Any]:
    out = {}
    for k, v in row.items():
        if hasattr(v, "isoformat"):
            v = v.isoformat()[:5] if k == "start" else v.isoformat()
        elif v is not None and type(v).__name__ == "Decimal":
            v = float(v)
        out[k] = v
    return out


@app.get("/offices/{office_id}/opportunities")
def office_opportunities(office_id: str, days: int = Query(14, ge=1, le=14)) -> dict[str, Any]:
    """The slot board: who to offer each open slot in the next two weeks, and booked visits at risk of being
    missed, each with a backup patient for the slot (make optimize first)."""
    _, today = _synced_office(office_id)
    with office_session(engine(), office_id) as conn:
        slots = conn.execute(
            text(
                'SELECT date, start, provider_id, "column", minutes, patient_id, plan_id, code, p_book, p_show, '
                "margin, expected_value, reason FROM slot_plan "
                "WHERE date <= :last ORDER BY date, start, provider_id"
            ),
            {"last": today + timedelta(days=days)},
        ).mappings()
        slots = [_plain(r) for r in slots]
        at_risk = conn.execute(
            text(
                "SELECT appt_id, patient_id, date, start, provider_id, p_missed, value_at_risk, action, "
                "backup_patient_id, backup_code FROM at_risk_visits ORDER BY value_at_risk DESC"
            )
        ).mappings()
        at_risk = [_plain(r) for r in at_risk]
    return {
        "office_id": office_id,
        "as_of": today.isoformat(),
        "offers": len(slots),
        "expected_value": round(sum(s["expected_value"] for s in slots), 2),
        "slots": slots,
        "at_risk": at_risk,
    }
