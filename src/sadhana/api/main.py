"""FastAPI app. One app holds every layer as modules; this file wires the routes."""

from typing import Any

from fastapi import FastAPI, HTTPException

from sadhana import __version__
from sadhana.config import settings
from sadhana.economics.engine import build, load_inputs

app = FastAPI(title="Sadhana AI", version=__version__)


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
