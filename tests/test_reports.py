"""The monthly report: recovered counts only what the holdout comparison shows, and the page renders.

Needs Postgres (make up); skips locally without it, required in CI.
"""

import pytest
from sqlalchemy import text

from sadhana.agents.run import OUTREACH_TABLES, run_outreach
from sadhana.config import settings
from sadhana.data.db import office_session
from sadhana.models.run import run_office
from sadhana.optimizer.run import plan_office
from sadhana.reports.html import render
from sadhana.reports.monthly import build_report, load_settings
from sadhana.simulator.build import write
from sadhana.simulator.population import SIM_TODAY

OFFICE = "solo_manhattan"


@pytest.fixture(scope="module")
def report(engine, synced, solo_sim, tmp_path_factory):
    run_office(engine, OFFICE, SIM_TODAY)
    plan_office(engine, OFFICE, SIM_TODAY)
    run_outreach(engine, OFFICE, SIM_TODAY, days=20, sim_dir=write(solo_sim, tmp_path_factory.mktemp("sim")))
    return build_report(engine, OFFICE, SIM_TODAY)


def test_recovered_counts_only_the_measured_difference(engine, report) -> None:
    d = report.as_dict()
    assert d["recovered_total"] == pytest.approx(sum(r["collected"] for r in d["recovered"]), abs=0.01)
    assert d["return_on_fee"] == pytest.approx(
        d["recovered_total"] / load_settings(settings.configs_dir)["monthly_fee"], abs=0.1
    )
    measured = [r for r in d["recovered"] if r["measured"]]
    assert {r["source"] for r in measured} >= {"Recall outreach", "Treatment follow-up"}
    for r in measured:
        assert r["extra_bookings"] <= (r["rate_with_outreach"] - r["rate_holdout"]) * r["people_reached"] + 0.1
        assert 0 <= r["margin"] <= r["collected"] + 1e-6
    booked = d["outreach"]["booked_by_text"]
    assert sum(r["extra_bookings"] for r in d["recovered"]) < booked + sum(r["people_reached"] for r in measured)
    assert d["outreach"]["messages"] > 0 and d["desk"]["hours_saved"] > 0
    assert any("backlog" in n for n in d["notes"])
    low, high = d["recovered_range"]
    assert low <= d["recovered_total"] <= high


def test_nothing_is_claimed_without_a_holdout(engine, report) -> None:
    with engine.begin() as conn:
        saved = (
            conn.execute(text("SELECT * FROM sadhana_meta.lift_estimates WHERE office_id = :o"), {"o": OFFICE})
            .mappings()
            .all()
        )
        conn.execute(text("DELETE FROM sadhana_meta.lift_estimates WHERE office_id = :o"), {"o": OFFICE})
    try:
        bare = build_report(engine, OFFICE, SIM_TODAY).as_dict()
        assert all(not r["measured"] for r in bare["recovered"])  # only refills, flagged as not measured
    finally:
        with engine.begin() as conn:
            for r in saved:
                conn.execute(
                    text(
                        "INSERT INTO sadhana_meta.lift_estimates (office_id, lift_group, segment, as_of, n_outreach, "
                        "n_holdout, rate_outreach, rate_holdout, lift, ci_low, ci_high) VALUES (:office_id, "
                        ":lift_group, :segment, :as_of, :n_outreach, :n_holdout, :rate_outreach, :rate_holdout, :lift, "
                        ":ci_low, :ci_high)"
                    ),
                    dict(r),
                )


def test_page_and_endpoints(report) -> None:
    from fastapi.testclient import TestClient

    from sadhana.api.main import app

    html = render(report)
    assert "recovered this month" in html and "Where it came from" in html and "<script" not in html
    assert "likely range" in html and ("Who responds" in html or not report.segments)
    client = TestClient(app)
    body = client.get(f"/offices/{OFFICE}/report").json()
    assert body["recovered_total"] == pytest.approx(report.recovered_total, rel=0.01)
    page = client.get(f"/offices/{OFFICE}/report.html")
    assert page.status_code == 200 and page.headers["content-type"].startswith("text/html")


def test_report_is_empty_before_any_outreach(engine, report) -> None:
    with office_session(engine, OFFICE) as conn:
        for t in OUTREACH_TABLES:
            conn.execute(text(f"DELETE FROM {t}"))
    empty = build_report(engine, OFFICE, SIM_TODAY).as_dict()
    assert empty["recovered"] == [] and empty["recovered_total"] == 0 and empty["outreach"]["messages"] == 0
