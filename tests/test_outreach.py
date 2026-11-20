"""Outreach agent on the demo offices, with simulated patients answering. Graded against hidden truth.

Needs Postgres (make up); skips locally without it, required in CI.
"""

from pathlib import Path

import pytest
from sqlalchemy import text

from sadhana.agents.lift import latest
from sadhana.agents.outreach import in_holdout
from sadhana.agents.run import run_outreach
from sadhana.config import settings
from sadhana.data.db import office_session
from sadhana.models.run import run_office
from sadhana.optimizer.run import plan_office
from sadhana.simulator import responses
from sadhana.simulator.build import write
from sadhana.simulator.population import SIM_TODAY

OFFICE = "group_queens"


@pytest.fixture(scope="module")
def pilot(engine, synced, group_sim, tmp_path_factory):
    run_office(engine, OFFICE, SIM_TODAY)
    plan_office(engine, OFFICE, SIM_TODAY)
    sim_dir = Path(write(group_sim, tmp_path_factory.mktemp("truth")))  # the same simulation that was synced
    return run_outreach(engine, OFFICE, SIM_TODAY, days=20, sim_dir=sim_dir), sim_dir


def test_holdout_is_fixed_and_the_right_size() -> None:
    picks = [in_holdout("o", f"P{i:06d}", "recall", 0.2) for i in range(20_000)]
    assert 0.19 < sum(picks) / len(picks) < 0.21
    assert picks == [in_holdout("o", f"P{i:06d}", "recall", 0.2) for i in range(20_000)]


def test_nobody_is_over_messaged_or_messaged_after_stop(engine, pilot) -> None:
    with office_session(engine, OFFICE) as conn:
        per_day = conn.execute(
            text(
                "SELECT max(n) FROM (SELECT contact_id, sent_on, count(*) n FROM messages WHERE kind <> 'reply' "
                "GROUP BY 1, 2) x"
            )
        ).scalar()
        weekly = conn.execute(
            text(
                "SELECT max(n) FROM (SELECT a.contact_id, a.sent_on, count(*) n FROM messages a JOIN messages b "
                "ON a.contact_id = b.contact_id AND b.sent_on > a.sent_on - 7 AND b.sent_on <= a.sent_on "
                "WHERE a.kind <> 'reply' AND b.kind <> 'reply' GROUP BY 1, 2) x"
            )
        ).scalar()
        after_stop = conn.execute(
            text(
                "SELECT count(*) FROM messages m JOIN opt_outs o ON o.contact_id = m.contact_id "
                "AND o.channel = m.channel AND m.sent_on > o.opted_out_on AND m.body NOT LIKE '%unsubscribed%'"
            )
        ).scalar()
        held_out_messaged = conn.execute(
            text("SELECT count(*) FROM messages JOIN outreach_targets USING (target_id) WHERE arm = 'holdout'")
        ).scalar()
        no_consent = conn.execute(
            text(
                "SELECT count(*) FROM messages m JOIN patients c ON c.patient_id = m.contact_id "
                "WHERE (m.channel = 'sms' AND NOT c.sms_consent) OR (m.channel = 'email' AND NOT c.email_on_file)"
            )
        ).scalar()
    assert per_day == 1 and weekly <= 2
    assert after_stop == 0 and held_out_messaged == 0 and no_consent == 0
    assert pilot[0].opt_outs > 0  # some people do say STOP, and the inbound handler records it


def test_children_are_reached_through_guardians(engine, pilot) -> None:
    with office_session(engine, OFFICE) as conn:
        rows = conn.execute(
            text(
                "SELECT p.guardian_id, m.contact_id FROM messages m JOIN patients p ON p.patient_id = m.patient_id "
                "WHERE p.guardian_id IS NOT NULL AND p.birth_date > :d"
            ),
            {"d": SIM_TODAY.replace(year=SIM_TODAY.year - 17)},
        ).all()
    assert rows and all(g == c for g, c in rows)


def test_measured_lift_finds_the_hidden_truth(engine, pilot) -> None:
    result, sim_dir = pilot
    truth = responses.load_truth(sim_dir)
    cfg = responses.load_settings(settings.configs_dir)
    with office_session(engine, OFFICE) as conn:
        true = {g: responses.true_lift(conn, truth, cfg, g) for g in ("recall", "treatment")}
    est = {e["lift_group"]: e for e in result.estimates if not e["segment"]}
    for group, value in true.items():
        assert est[group]["ci_low"] <= value <= est[group]["ci_high"], (group, value, est[group])
        assert est[group]["n_holdout"] >= 100
    # the optimizer's guesses were too high; measuring is the point
    assert est["recall"]["lift"] < 0.3 and est["treatment"]["lift"] < 0.15


def test_lift_by_segment_finds_each_segments_truth(engine, pilot) -> None:
    """Regular patients answer more than new ones, lapsed ones far less (hidden); each measured segment lift sits
    within a few standard errors of its own truth."""
    result, sim_dir = pilot
    truth = responses.load_truth(sim_dir)
    cfg = responses.load_settings(settings.configs_dir)
    segs = [e for e in result.estimates if e["segment"] and e["lift_group"] in ("recall", "treatment")]
    assert {e["segment"] for e in segs} >= {"new", "established"}
    with office_session(engine, OFFICE) as conn:
        for e in segs:
            if e["n_holdout"] < 30:
                continue
            true = responses.true_lift(conn, truth, cfg, e["lift_group"], e["segment"])
            se = (e["ci_high"] - e["ci_low"]) / (2 * 1.96)
            assert abs(e["lift"] - true) <= 3.5 * se, (e, true)
        segments = {r[0] for r in conn.execute(text("SELECT DISTINCT segment FROM outreach_targets"))}
    assert segments <= {"new", "established", "lapsed"}


def test_optimizer_switches_to_the_measured_lift(engine, pilot) -> None:
    measured = latest(engine, OFFICE, 100)
    assert set(measured) >= {"recall", "treatment"}
    plan = plan_office(engine, OFFICE, SIM_TODAY).summary()
    assert plan["lift_source"]["recall"] == "measured"
    assert plan["outreach_lift"]["recall"] == pytest.approx(measured["recall"])


def test_replies_book_without_double_booking_and_refills_have_one_winner(engine, pilot) -> None:
    result, _ = pilot
    assert result.replies.get("booked", 0) > 50 and result.replies.get("refilled", 0) > 0
    with office_session(engine, OFFICE) as conn:
        overlaps = conn.execute(
            text(
                "WITH x AS (SELECT provider_id, date, start, start + make_interval(mins => minutes) e FROM "
                "pending_bookings WHERE kind = 'book' UNION ALL SELECT provider_id, date, start, \"end\" FROM "
                "appointments WHERE status = 'scheduled' AND appt_id NOT IN (SELECT appt_id FROM pending_bookings "
                "WHERE kind = 'cancel')) SELECT count(*) FROM x a JOIN x b ON a.provider_id = b.provider_id "
                "AND a.date = b.date AND a.start < b.start AND a.e > b.start"
            )
        ).scalar()
        winners = conn.execute(
            text(
                "SELECT max(n) FROM (SELECT slot_key, count(*) n FROM refill_offers WHERE status = 'won' GROUP BY 1) x"
            )
        ).scalar()
        twice = conn.execute(
            text("SELECT count(*) FROM (SELECT patient_id FROM pending_bookings WHERE kind = 'book' GROUP BY 1 "
                 "HAVING count(*) > 1) x")
        ).scalar()  # fmt: skip
        replies_after_stop = conn.execute(
            text(
                "SELECT count(*) FROM messages m JOIN opt_outs o ON o.contact_id = m.contact_id AND o.channel = "
                "m.channel WHERE m.kind = 'reply' AND m.body NOT LIKE '%unsubscribed%' AND (m.sent_on > o.opted_out_on)"
            )
        ).scalar()
    assert overlaps == 0 and winners == 1 and twice == 0 and replies_after_stop == 0


def test_outreach_and_inbound_endpoints(engine, pilot) -> None:
    from fastapi.testclient import TestClient

    from sadhana.api.main import app

    client = TestClient(app)
    body = client.get(f"/offices/{OFFICE}/outreach").json()
    assert body["messages"]["sms"] > 0 and body["targets"]["recall"]["held_out"] > 0
    assert {r["lift_group"] for r in body["lift"]} >= {"recall", "treatment"}
    with office_session(engine, OFFICE) as conn:
        contact = conn.execute(
            text("SELECT patient_id FROM patients WHERE patient_id NOT IN (SELECT contact_id FROM opt_outs) LIMIT 1")
        ).scalar()
    r = client.post(f"/offices/{OFFICE}/inbound", json={"contact_id": contact, "body": "STOP"}).json()
    assert r["action"] == "opt_out" and "START" in r["auto_reply"]
    r = client.post(f"/offices/{OFFICE}/inbound", json={"contact_id": contact, "body": "START"}).json()
    assert r["action"] == "opt_in"
    assert client.post(f"/offices/{OFFICE}/inbound", json={"contact_id": "nobody", "body": "hi"}).status_code == 404
