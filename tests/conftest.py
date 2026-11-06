from pathlib import Path

import pytest

from sadhana.config import settings
from sadhana.economics.engine import OfficeInputs, load_inputs

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
