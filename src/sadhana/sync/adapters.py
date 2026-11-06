"""Adapters: one shape every data source plugs into.

An adapter knows how to read one kind of source and hand back rows already named like the canonical tables.
The sync job never knows where rows came from. Today: the simulator's output files. Next: Open Dental.
"""

from __future__ import annotations

import csv
import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any, Protocol

Row = dict[str, Any]


class Adapter(Protocol):
    office_id: str
    source: str

    def office(self) -> Row:
        """The office's config: name, hours, staff, policies."""
        ...

    def rows(self, table: str) -> Iterator[Row]:
        """Rows for one canonical table, keyed by column name. Missing columns load as empty."""
        ...


# canonical table -> simulator output file
SIM_FILES = {
    "patients": "patients.csv",
    "patient_status": "patient_history.csv",
    "appointments": "appointments.csv",
    "procedures": "procedures.csv",
    "teeth": "teeth.csv",
    "conditions": "conditions.csv",
    "perio": "perio.csv",
    "treatment_plan": "treatment_plan.csv",
    "ledger": "ledger.csv",
    "claims": "claims.csv",
    "payments": "payments.csv",
    "benefits": "benefits.csv",
    "balances": "balances.csv",
}


class SimulatorAdapter:
    """Reads `make sim` output like a PMS export. Hidden truth files are never read."""

    source = "simulator"

    def __init__(self, office_id: str, sim_dir: Path, configs_dir: Path) -> None:
        self.office_id = office_id
        self.sim_dir = sim_dir
        self.configs_dir = configs_dir
        missing = [f for f in SIM_FILES.values() if not (sim_dir / f).exists()]
        if missing:
            raise FileNotFoundError(f"run `make sim` first; missing in {sim_dir}: {', '.join(missing)}")

    def office(self) -> Row:
        for folder in ("offices", "offices_holdout"):
            path = self.configs_dir / folder / f"{self.office_id}.json"
            if path.exists():
                return json.loads(path.read_text())
        raise FileNotFoundError(self.office_id)

    def rows(self, table: str) -> Iterator[Row]:
        if table == "staff":
            for s in self.office()["staff"]:
                yield {**s, "staff_id": s["id"]}
            return
        with (self.sim_dir / SIM_FILES[table]).open(newline="") as f:
            for row in csv.DictReader(f):
                if table == "conditions" and row.get("diagnosed") != "True":
                    continue  # the office only knows what an exam charted
                yield row
