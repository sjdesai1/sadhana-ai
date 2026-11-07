"""FastAPI app. One app holds every layer as modules; this file wires the routes."""

from datetime import date
from functools import lru_cache
from typing import Any

from fastapi import FastAPI, HTTPException, Query
from sqlalchemy import Engine, text
from sqlalchemy.exc import OperationalError

from sadhana import __version__
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
