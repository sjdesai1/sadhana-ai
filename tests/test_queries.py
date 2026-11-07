"""Questions answered from the database match what the simulator says happened.

Needs Postgres (make up); skips locally without it, required in CI.
"""

from datetime import date

import pytest
from fastapi.testclient import TestClient

from sadhana.api.main import app
from sadhana.config import settings
from sadhana.data import queries
from sadhana.data.db import office_session
from sadhana.simulator.population import SIM_TODAY
from sadhana.simulator.schedule import add_months

client = TestClient(app)


@pytest.fixture(scope="module")
def fees(solo_sim):
    return queries.office_fees(solo_sim.office, settings.configs_dir)


def test_overdue_list_matches_patient_records(engine, synced, solo_sim) -> None:
    maint = {x.patient_id for x in solo_sim.perio if x.maintenance}
    expected = {
        h.patient_id
        for h in solo_sim.history
        if h.status == "active"
        and h.last_cleaning
        and not h.next_cleaning
        and date.fromisoformat(h.last_cleaning) < add_months(SIM_TODAY, -(4 if h.patient_id in maint else 7))
    }
    with office_session(engine, "solo_manhattan") as conn:
        rows = queries.overdue(conn, SIM_TODAY)
    assert {r["patient_id"] for r in rows} == expected
    assert [r["last_cleaning"] for r in rows] == sorted(r["last_cleaning"] for r in rows)
    assert all(r["days_overdue"] > 0 for r in rows)


def test_unscheduled_treatment_adds_up(engine, synced, solo_sim, fees) -> None:
    archived = {h.patient_id for h in solo_sim.history if h.status == "archived"}
    expected = sum(
        fees[x.code] for x in solo_sim.plan if x.status in ("proposed", "accepted") and x.patient_id not in archived
    )
    with office_session(engine, "solo_manhattan") as conn:
        rows = queries.unscheduled_treatment(conn, fees)
    assert abs(sum(r["value"] for r in rows) - expected) < 1
    assert [r["value"] for r in rows] == sorted((r["value"] for r in rows), reverse=True)


def test_unused_benefits_match_the_benefits_ledger(engine, synced, solo_sim, fees) -> None:
    active = {h.patient_id for h in solo_sim.history if h.status == "active"}
    expected = sum(
        b.remaining
        for b in solo_sim.money.benefits
        if b.year == SIM_TODAY.year and b.rank == "primary" and b.patient_id in active and b.remaining > 0
    )
    with office_session(engine, "solo_manhattan") as conn:
        rows = queries.unused_benefits(conn, SIM_TODAY, fees)
    assert abs(sum(r["remaining"] for r in rows) - expected) < 1
    assert all(0 <= r["at_stake"] <= r["remaining"] for r in rows)


def test_summary_endpoint(synced) -> None:
    resp = client.get("/offices/solo_manhattan/summary")
    assert resp.status_code == 200
    body = resp.json()
    assert body["as_of"] == SIM_TODAY.isoformat()
    assert body["patients_on_file"] == synced["solo_manhattan"]["patients"]
    assert 0 < body["overdue_for_cleaning"] < body["active_patients"]
    assert body["unscheduled_treatment_value"] > 100_000
    assert body["unused_benefits_value"] > body["benefits_at_stake_value"] > 0


def test_list_endpoints_respect_the_limit(synced) -> None:
    for path in ("overdue", "unscheduled-treatment", "unused-benefits"):
        resp = client.get(f"/offices/group_queens/{path}?limit=5")
        assert resp.status_code == 200 and len(resp.json()) == 5, path


def test_offices_without_data_return_404(synced) -> None:
    assert client.get("/offices/holdout_bronx_medicaid/summary").status_code == 404
    assert client.get("/offices/../summary").status_code == 404
