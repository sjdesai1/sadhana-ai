"""Conversations, refills and the front desk queue.

Needs Postgres (make up); skips locally without it, required in CI.
"""

from datetime import date, timedelta

import pytest
from sqlalchemy import insert, text

from sadhana.agents import desk
from sadhana.agents.outreach import approve
from sadhana.agents.replies import ReplyDesk
from sadhana.agents.run import OUTREACH_TABLES
from sadhana.config import settings
from sadhana.data.db import office_session
from sadhana.data.schema import messages, outreach_targets
from sadhana.messaging.intents import intent
from sadhana.messaging.policy import load_settings
from sadhana.models.run import run_office
from sadhana.optimizer.run import plan_office
from sadhana.simulator.population import SIM_TODAY

S = load_settings(settings.configs_dir)
OFFICE = "group_queens"
DAY = date(2026, 11, 2)  # a Monday


@pytest.mark.parametrize(
    ("body", "expected"),
    [("C", "confirm"), ("confirm", "confirm"), ("R", "reschedule"), ("Can I do Tuesday instead?", "reschedule"),
     ("Yes", "accept"), ("sí", "accept"), ("sure!", "accept"), ("1", "choose"), ("2.", "choose"),
     ("No thanks", "decline"), ("not right now", "decline"), ("How much will it cost?", "question"),
     ("Does insurance cover it", "question"), ("STOP", "opt_out"), ("CANCEL", "opt_out"), ("help", "help"),
     ("ok see you", "accept"), ("great", "other")],
)  # fmt: skip
def test_reply_intents(body, expected) -> None:
    assert intent(body, S) == expected


@pytest.fixture(scope="module")
def office(engine, synced):
    run_office(engine, OFFICE, SIM_TODAY)
    plan_office(engine, OFFICE, SIM_TODAY)
    with engine.connect() as conn:
        return conn.execute(
            text("SELECT config FROM sadhana_meta.offices WHERE office_id = :o"), {"o": OFFICE}
        ).scalar()


@pytest.fixture
def conn(engine, office):
    with office_session(engine, OFFICE) as c:
        for t in OUTREACH_TABLES:
            c.execute(text(f"DELETE FROM {t}"))
        yield c


def _texters(conn, n: int, booked: bool = False) -> list[str]:
    """Adults who take texts; with or without a visit already booked."""
    cond = "IN" if booked else "NOT IN"
    return [
        r[0]
        for r in conn.execute(
            text(
                "SELECT patient_id FROM patients WHERE sms_consent AND phone_type = 'cell' AND guardian_id IS NULL "
                f"AND birth_date < '2000-01-01' AND patient_id {cond} (SELECT patient_id FROM appointments "
                "WHERE status = 'scheduled' AND date > :d) ORDER BY patient_id LIMIT :n"
            ),
            {"d": DAY, "n": n},
        )
    ]


def _sent(conn, patient: str, purpose: str, ref: str, code: str = "") -> None:
    tid = f"t-{purpose}-{patient}"
    conn.execute(insert(outreach_targets).values(
        office_id=OFFICE, target_id=tid, patient_id=patient, purpose=purpose, lift_group=purpose, ref_id=ref,
        code=code, targeted_on=DAY, arm="outreach", status="messaged"))  # fmt: skip
    conn.execute(insert(messages).values(
        office_id=OFFICE, message_id=f"m-{tid}", target_id=tid, kind="outreach", patient_id=patient,
        contact_id=patient, channel="sms", body="(test)", sent_on=DAY, status="sent"))  # fmt: skip


def _no_overlaps(conn) -> None:
    n = conn.execute(
        text(
            "WITH x AS (SELECT provider_id, date, start, start + make_interval(mins => minutes) e FROM "
            "pending_bookings WHERE kind = 'book' UNION ALL SELECT provider_id, date, start, \"end\" FROM "
            "appointments WHERE status = 'scheduled' AND appt_id NOT IN (SELECT appt_id FROM pending_bookings "
            "WHERE kind = 'cancel')) SELECT count(*) FROM x a JOIN x b ON a.provider_id = b.provider_id "
            "AND a.date = b.date AND a.start < b.start AND a.e > b.start"
        )
    ).scalar()
    assert n == 0


def test_yes_to_recall_offers_two_times_and_a_pick_books_it(conn, office) -> None:
    (p,) = _texters(conn, 1)
    _sent(conn, p, "recall", f"recall-{p}")
    agent = ReplyDesk(conn, office, S, DAY)
    r = agent.receive(p, "sms", "Yes")
    assert r["intent"] == "accept" and "Reply 1 or 2" in r["auto_reply"]
    r = agent.receive(p, "sms", "2")
    assert r["auto_reply"].startswith("You're booked")
    row = conn.execute(text("SELECT code, source, date FROM pending_bookings WHERE patient_id = :p"), {"p": p}).one()
    assert row.code in ("D1110", "D4910") and row.source == "reply" and row.date > DAY
    _no_overlaps(conn)


def test_reschedule_frees_the_slot_offers_it_to_backups_and_the_first_yes_wins(conn, office) -> None:
    (p,) = _texters(conn, 1, booked=True)
    appt = conn.execute(
        text("SELECT appt_id FROM appointments WHERE patient_id = :p AND status = 'scheduled' AND date > :d "
             "ORDER BY date LIMIT 1"), {"p": p, "d": DAY + timedelta(days=2)}).scalar()  # fmt: skip
    _sent(conn, p, "confirm", appt)
    agent = ReplyDesk(conn, office, S, DAY)
    r = agent.receive(p, "sms", "Can I do another day instead?")
    assert r["intent"] == "reschedule" and "Reply 1 or 2" in r["auto_reply"]
    assert conn.execute(text("SELECT count(*) FROM pending_bookings WHERE kind = 'cancel' AND appt_id = :a"),
                        {"a": appt}).scalar() == 1  # fmt: skip
    offers = conn.execute(text("SELECT offer_id, contact_id FROM refill_offers ORDER BY offer_id")).all()
    assert 1 <= len(offers) <= S["refill"]["offers_at_once"]
    assert p not in {c for _, c in offers}
    first = agent.receive(offers[0].contact_id, "sms", "Yes")
    assert first["auto_reply"].startswith("You're booked")
    if len(offers) > 1:
        second = agent.receive(offers[1].contact_id, "sms", "Yes")
        assert "just taken" in second["auto_reply"]
    assert conn.execute(text("SELECT count(*) FROM refill_offers WHERE status = 'won'")).scalar() == 1
    agent.receive(p, "sms", "1")
    _no_overlaps(conn)


def test_confirmations_questions_and_escalation(conn, office) -> None:
    a, q = _texters(conn, 2, booked=True)
    appt = conn.execute(text("SELECT appt_id FROM appointments WHERE patient_id = :p AND status = 'scheduled' "
                             "AND date > :d LIMIT 1"), {"p": a, "d": DAY}).scalar()  # fmt: skip
    _sent(conn, a, "confirm", appt)
    agent = ReplyDesk(conn, office, S, DAY)
    assert agent.receive(a, "sms", "C")["auto_reply"].startswith("Thanks, you're confirmed")
    _sent(conn, q, "recall", f"recall-{q}")
    r = agent.receive(q, "sms", "How much will it cost with my insurance?")
    assert r["intent"] == "question" and "get back to you" in r["auto_reply"]
    task = conn.execute(text("SELECT task_id, level FROM desk_tasks WHERE kind = 'needs_reply'")).one()
    assert task.level == "front_desk"
    assert desk.escalate(conn, S, DAY + timedelta(days=1)) == 0  # still within the front desk's day
    desk.escalate(conn, S, DAY + timedelta(days=2))
    desk.escalate(conn, S, DAY + timedelta(days=4))
    level = conn.execute(text("SELECT level FROM desk_tasks WHERE task_id = :i"), {"i": task.task_id}).scalar()
    assert level == "dentist"
    assert conn.execute(text("SELECT count(*) FROM desk_tasks WHERE kind = 'fyi' AND status = 'open'")).scalar() == 0


def test_no_reply_after_stop_and_cosmetic_waits_for_approval(conn, office) -> None:
    p, c = _texters(conn, 2)
    _sent(conn, p, "recall", f"recall-{p}")
    agent = ReplyDesk(conn, office, S, DAY)
    assert agent.receive(p, "sms", "STOP")["action"] == "opt_out"
    assert agent.receive(p, "sms", "Yes")["auto_reply"] == ""
    assert conn.execute(text("SELECT count(*) FROM messages WHERE kind = 'reply' AND contact_id = :p "
                             "AND body NOT LIKE '%unsubscribed%'"), {"p": p}).scalar() == 0  # fmt: skip
    # cosmetic follow-up: nothing is sent until the dentist approves, and the rules are checked again then
    conn.execute(text("UPDATE patients SET marketing_consent = true WHERE patient_id = :c"), {"c": c})
    conn.execute(insert(outreach_targets).values(
        office_id=OFFICE, target_id="t-cos", patient_id=c, purpose="treatment", lift_group="treatment",
        ref_id="T-test", code="D9972", targeted_on=DAY, arm="outreach", status="needs_approval"))  # fmt: skip
    desk.add(conn, OFFICE, S, "approval", c, "Approve cosmetic follow-up text", DAY, c, "t-cos", "dentist")
    task = conn.execute(text("SELECT task_id FROM desk_tasks WHERE kind = 'approval'")).scalar()
    assert conn.execute(text("SELECT count(*) FROM messages WHERE target_id = 't-cos'")).scalar() == 0
    assert approve(conn, office, S, task, DAY)["sent"]
    assert conn.execute(text("SELECT count(*) FROM messages WHERE target_id = 't-cos'")).scalar() == 1
    assert not approve(conn, office, S, task, DAY)["sent"]  # already done


def test_desk_and_cancellation_endpoints(engine, office) -> None:
    from fastapi.testclient import TestClient

    from sadhana.api.main import app

    client = TestClient(app)
    with office_session(engine, OFFICE) as c:
        for t in OUTREACH_TABLES:
            c.execute(text(f"DELETE FROM {t}"))
        appt = c.execute(text("SELECT appt_id FROM appointments WHERE status = 'scheduled' AND date > :d "
                              "ORDER BY date LIMIT 1"), {"d": SIM_TODAY + timedelta(days=3)}).scalar()  # fmt: skip
    r = client.post(f"/offices/{OFFICE}/cancellations", json={"appt_id": appt}).json()
    assert r["freed"]
    assert not client.post(f"/offices/{OFFICE}/cancellations", json={"appt_id": appt}).json()["freed"]
    queue = client.get(f"/offices/{OFFICE}/desk?status=all").json()
    assert any("cancelled" in t["summary"] for t in queue["fyi"])
    # the simulated office's today is a Sunday: no texts go out, so the desk gets the slot to fill
    assert not r["offered"] and any("call the waitlist" in t["summary"] for t in queue["needs_action"])
    with office_session(engine, OFFICE) as c:
        p = c.execute(text("SELECT patient_id FROM patients ORDER BY patient_id LIMIT 1")).scalar()
    r = client.post(f"/offices/{OFFICE}/inbound", json={"contact_id": p, "body": "What time do you open?"}).json()
    assert r["intent"] == "question"
    task = next(t for t in client.get(f"/offices/{OFFICE}/desk").json()["needs_action"] if t["kind"] == "needs_reply")
    assert client.post(f"/offices/{OFFICE}/desk/{task['task_id']}/resolve").json()["status"] == "done"
    assert client.post(f"/offices/{OFFICE}/desk/{task['task_id']}/resolve").status_code == 404
