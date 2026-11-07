import os
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.exc import OperationalError

from sadhana.config import settings
from sadhana.data.db import get_engine
from sadhana.economics.engine import OfficeInputs, load_inputs
from sadhana.simulator.build import write
from sadhana.sync.adapters import SimulatorAdapter
from sadhana.sync.run import sync_office

CONFIGS: Path = settings.configs_dir


def _load(office_id: str) -> OfficeInputs:
    return load_inputs(CONFIGS / "offices" / f"{office_id}.json", CONFIGS)


@pytest.fixture(scope="session")
def solo() -> OfficeInputs:
    return _load("solo_manhattan")


@pytest.fixture(scope="session")
def group() -> OfficeInputs:
    return _load("group_queens")


@pytest.fixture(scope="session")
def solo_sim():
    from sadhana.simulator.build import simulate

    return simulate("solo_manhattan", CONFIGS, seed=42)


@pytest.fixture(scope="session")
def group_sim():
    from sadhana.simulator.build import simulate

    return simulate("group_queens", CONFIGS, seed=42)


@pytest.fixture(scope="session")
def holdout_sims():
    from sadhana.simulator.build import simulate

    names = ("holdout_bronx_medicaid", "holdout_park_slope_family", "holdout_ues_boutique")
    return {n: simulate(n, CONFIGS, seed=42) for n in names}


@pytest.fixture(scope="session")
def engine():
    eng = get_engine()
    try:
        with eng.connect() as conn:
            conn.execute(text("SELECT 1"))
    except OperationalError:
        if os.environ.get("SADHANA_REQUIRE_DB") == "1":
            raise
        pytest.skip("Postgres is not running (make up)")
    return eng


@pytest.fixture(scope="session")
def synced(engine, solo_sim, group_sim, tmp_path_factory):
    counts = {}
    for sim in (solo_sim, group_sim):
        office_id = sim.office["office_id"]
        out = write(sim, tmp_path_factory.mktemp(office_id))
        counts[office_id] = sync_office(engine, SimulatorAdapter(office_id, out, settings.configs_dir))
    return counts
