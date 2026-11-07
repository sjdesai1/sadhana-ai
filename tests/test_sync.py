"""Sync into Postgres: canonical tables per office, hidden truth kept out, offices isolated from each other.

Needs Postgres: locally `make up` first (these tests skip without it); CI runs a Postgres service and sets
SADHANA_REQUIRE_DB=1 so they can never be skipped there.
"""

from decimal import Decimal

import pytest
from sqlalchemy import text

from sadhana.config import settings
from sadhana.data.db import office_session
from sadhana.simulator.build import write
from sadhana.sync.adapters import SimulatorAdapter
from sadhana.sync.run import sync_office

TRUTH_COLUMNS = {"true_stage", "diagnosed", "recall_compliance", "no_show_rate", "cause", "left_reason", "age"}


def test_row_counts_match_the_source(synced, solo_sim) -> None:
    c = synced["solo_manhattan"]
    assert c["patients"] == len(solo_sim.patients)
    assert c["appointments"] == len(solo_sim.appointments)
    assert c["procedures"] == len(solo_sim.procedures)
    assert c["conditions"] == sum(x.diagnosed for x in solo_sim.conditions)
    assert c["staff"] == len(solo_sim.office["staff"])


def test_money_survives_the_trip(engine, synced, solo_sim) -> None:
    with office_session(engine, "solo_manhattan") as conn:
        fees = conn.execute(text("SELECT sum(fee), sum(patient_portion) FROM ledger")).one()
    assert fees[0] == sum(Decimal(str(x.fee)) for x in solo_sim.money.ledger)
    assert abs(fees[1] - sum(Decimal(str(x.patient_portion)) for x in solo_sim.money.ledger)) < Decimal("0.05")


def test_hidden_truth_never_reaches_the_database(engine, synced) -> None:
    with engine.connect() as conn:
        cols = {
            r[0]
            for r in conn.execute(
                text("SELECT column_name FROM information_schema.columns WHERE table_schema LIKE 'office_%'")
            )
        }
    assert not cols & TRUTH_COLUMNS


def test_an_office_session_sees_only_its_own_rows(engine, synced) -> None:
    with office_session(engine, "solo_manhattan") as conn:
        own = conn.execute(text("SELECT count(*) FROM patients")).scalar()
        other = conn.execute(text("SELECT count(*) FROM office_group_queens.patients")).scalar()
    assert own == synced["solo_manhattan"]["patients"] and other == 0
    with office_session(engine, "group_queens") as conn:
        assert conn.execute(text("SELECT count(*) FROM office_solo_manhattan.appointments")).scalar() == 0


def test_an_office_session_cannot_write_into_another_office(engine, synced) -> None:
    with pytest.raises(Exception, match="row-level security"), office_session(engine, "solo_manhattan") as conn:
        conn.execute(
            text("INSERT INTO office_group_queens.balances (office_id, guarantor_id) VALUES ('group_queens', 'X1')")
        )


def test_sync_is_repeatable_and_logged(engine, synced, solo_sim, tmp_path) -> None:
    out = write(solo_sim, tmp_path)
    again = sync_office(engine, SimulatorAdapter("solo_manhattan", out, settings.configs_dir))
    assert again == synced["solo_manhattan"]
    with engine.connect() as conn:
        status = conn.execute(
            text("SELECT status FROM sadhana_meta.sync_runs WHERE office_id = 'solo_manhattan' ORDER BY run_id DESC")
        ).scalar()
        office = conn.execute(
            text("SELECT source FROM sadhana_meta.offices WHERE office_id = 'solo_manhattan'")
        ).scalar()
    assert status == "ok" and office == "simulator"


def test_a_failed_sync_keeps_yesterdays_data(engine, synced, solo_sim, tmp_path) -> None:
    out = write(solo_sim, tmp_path)

    class Broken(SimulatorAdapter):
        def rows(self, table: str):
            if table == "claims":
                raise RuntimeError("source went away")
            yield from super().rows(table)

    with pytest.raises(RuntimeError):
        sync_office(engine, Broken("solo_manhattan", out, settings.configs_dir))
    with office_session(engine, "solo_manhattan") as conn:
        assert conn.execute(text("SELECT count(*) FROM appointments")).scalar() == len(solo_sim.appointments)
    with engine.connect() as conn:
        row = conn.execute(
            text(
                "SELECT status, error FROM sadhana_meta.sync_runs WHERE office_id='solo_manhattan' ORDER BY run_id DESC"
            )
        ).first()
    assert row is not None and row[0] == "failed" and "source went away" in row[1]
