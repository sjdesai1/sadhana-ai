"""Simulated patients answering outreach, from their hidden traits (decision 0006: the product never sees them).

For every target, outreach group or holdout, the chance of acting in the window with no outreach comes from
hidden traits (recall compliance, trust, price sensitivity, no-show and late-cancel rates). Outreach moves a share
of the people who would not have acted, depending on the channel, how responsive and trusting the person is, and
how price sensitive they are for treatment. Texts can also draw a STOP, more likely the more messages someone has
had lately; replies and opt-outs go through the product's own inbound handler.

`true_lift` gives the answer the measurement should find, for tests.
"""

from __future__ import annotations

import csv
import json
import random
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import Connection, insert, text

from sadhana.agents import desk
from sadhana.agents.outreach import approve
from sadhana.agents.replies import ReplyDesk
from sadhana.data.schema import outreach_results

Row = dict[str, Any]


def load_truth(sim_dir: Path) -> dict[str, Row]:
    with (sim_dir / "truth.csv").open(newline="") as f:
        return {r["patient_id"]: {k: _num(v) for k, v in r.items()} for r in csv.DictReader(f)}


def _num(v: str) -> Any:
    try:
        return float(v)
    except ValueError:
        return v


def load_settings(configs_dir: Path) -> Row:
    return json.loads((configs_dir / "simulator" / "responses.json").read_text())


def chances(t: Row, purpose: str, group: str, channel: str, cfg: Row) -> tuple[float, float]:
    """(chance of success with no outreach, chance with this outreach) for one person."""
    nat, lift = cfg["natural"], cfg["lift"]
    if group == "confirm":
        miss = min(0.9, t["no_show_rate"] + t["late_cancel_rate"])
        base = 1 - miss
        if not channel:
            return base, base
        cut = lift["confirm_effect"] * lift["channel"][channel] * (0.4 + t["text_responsiveness"])
        return base, 1 - miss * max(0.0, 1 - min(cut, 0.9))
    if group == "recall":
        base = t["recall_compliance"] * nat["recall_scale"]
        effect = lift["recall"]
    else:
        base = t["trust_baseline"] * (1 - t["price_sensitivity"] / 2) * nat["treatment_scale"]
        effect = lift["treatment"] * (1.4 - t["price_sensitivity"])
    if not channel:
        return base, base
    effect *= lift["channel"][channel] * (0.5 + t["text_responsiveness"]) * (0.6 + 0.8 * t["trust_baseline"])
    if purpose == "slot_offer":
        effect *= lift["slot_offer_factor"]
    effect = min(effect, 0.9)
    return base, base + (1 - base) * effect


def respond(
    conn: Connection, office: Row, msg_settings: Row, truth: dict[str, Row], cfg: Row, day: date, seed: int,
    agent: ReplyDesk | None = None,
) -> Row:  # fmt: skip
    """Results for every target picked on `day`, plus replies and opt-outs for the messages sent that day. Replies
    go through the product's reply agent, so a "Yes" books, an "R" frees a slot that is offered to backups, and
    backups answer those offers the same day."""
    rng = random.Random(f"{seed}|{office['office_id']}|{day}")
    if agent is None:
        agent = ReplyDesk(conn, office, msg_settings, day)
    agent.new_day(day)
    before = dict(agent.counts)
    targets = (
        conn.execute(
            text(
                "SELECT t.target_id, t.patient_id, t.purpose, t.lift_group, t.status, m.channel, m.contact_id "
                "FROM outreach_targets t LEFT JOIN messages m USING (target_id) "
                "WHERE t.targeted_on = :d ORDER BY t.target_id"
            ),
            {"d": day},
        )
        .mappings()
        .all()
    )
    recent = dict(
        conn.execute(
            text(
                "SELECT contact_id, count(*) FROM messages WHERE sent_on > :d AND sent_on < :t "
                "AND coalesce(kind, '') <> 'reply' GROUP BY contact_id"
            ),
            {"d": day - timedelta(days=30), "t": day},
        ).all()
    )
    rows, stops = [], 0

    def pick_if_offered(contact: str) -> None:
        state = conn.execute(text("SELECT state FROM conversations WHERE contact_id = :c"), {"c": contact}).scalar()
        if state == "offered" and rng.random() < cfg["pick_option"]:
            agent.receive(contact, "sms", rng.choice(["1", "1", "2"]))

    for r in targets:
        t = truth.get(r["patient_id"])
        if t is None:
            continue
        channel = r["channel"] or ("desk_call" if r["status"] == "desk_call" else "")
        base, p = chances(t, r["purpose"], r["lift_group"], channel, cfg)
        success = rng.random() < p
        rows.append(
            {
                "office_id": office["office_id"],
                "target_id": r["target_id"],
                "patient_id": r["patient_id"],
                "success": success,
                "observed_on": day + timedelta(days=cfg["window_days"]),
            }
        )
        if r["channel"] not in ("sms", "email"):
            continue
        contact_id = r["contact_id"]
        contact = truth.get(contact_id, t)
        o = cfg["opt_out"]
        if r["channel"] == "sms":
            p_stop = o["sms_base"] * (1 + o["per_recent_message"] * recent.get(contact_id, 0))
            p_stop *= 1.6 - contact["trust_baseline"]
        else:
            p_stop = o["email_base"]
        if rng.random() < p_stop:
            body = (
                rng.choice(["STOP", "stop", "Unsubscribe", "Please stop texting me"])
                if r["channel"] == "sms"
                else "unsubscribe"
            )
            agent.receive(contact_id, r["channel"], body)
            stops += 1
            continue
        if r["channel"] != "sms" or rng.random() >= contact["text_responsiveness"]:
            continue
        if success:
            agent.receive(contact_id, "sms", "C" if r["purpose"] == "confirm" else rng.choice(["Yes", "yes", "Sure"]))
            pick_if_offered(contact_id)
        elif r["purpose"] == "confirm" and rng.random() < cfg["reschedule_share"]:
            agent.receive(contact_id, "sms", rng.choice(["R", "Can I do another day instead?"]))
            pick_if_offered(contact_id)
        elif rng.random() < cfg["reply_when_not_booking"]:
            question = rng.random() < cfg["question_share"]
            body = rng.choice(QUESTIONS) if question else rng.choice(["Not right now, thanks", "No thanks"])
            agent.receive(contact_id, "sms", body)
    if rows:
        conn.execute(insert(outreach_results), rows)

    # some booked patients cancel ahead of time; the PMS reports it and the slot is offered to backups
    upcoming = conn.execute(
        text(
            "SELECT appt_id FROM appointments WHERE status = 'scheduled' AND date > :d AND date <= :e "
            "AND appt_id NOT IN (SELECT appt_id FROM pending_bookings WHERE kind = 'cancel') ORDER BY appt_id"
        ),
        {"d": day + timedelta(days=1), "e": day + timedelta(days=14)},
    ).all()
    for (appt_id,) in upcoming:
        if rng.random() < cfg["advance_cancel_per_day"]:
            agent.cancellation(appt_id)

    # backups answer refill offers made today; the first yes gets the slot
    offers = conn.execute(
        text(
            "SELECT o.patient_id, o.contact_id, o.plan_id, m.channel FROM refill_offers o "
            "JOIN messages m ON m.ref_id = o.offer_id WHERE o.offered_on = :d AND o.status = 'offered' "
            "ORDER BY o.offer_id"
        ),
        {"d": day},
    ).all()
    offers = list(offers)
    rng.shuffle(offers)
    for pid, contact_id, plan_id, channel in offers:
        t = truth.get(pid)
        if t is None or channel != "sms":
            continue
        _, p = chances(t, "slot_offer", "treatment" if plan_id else "recall", channel, cfg)
        if rng.random() < p and rng.random() < truth.get(contact_id, t)["text_responsiveness"]:
            agent.receive(contact_id, "sms", "Yes")
            pick_if_offered(contact_id)
    today = {k: v - before.get(k, 0) for k, v in agent.counts.items() if v - before.get(k, 0)}
    return {"results": len(rows), "opt_outs": stops, **today}


QUESTIONS = [
    "How much will it cost with my insurance?",
    "Does my insurance cover this?",
    "Can my daughter come at the same time?",
    "What is this for exactly?",
]


def work_desk(conn: Connection, office: Row, msg_settings: Row, cfg: Row, day: date, seed: int) -> Row:
    """The simulated front desk works its queue: answers and calls get done some days, not others; the dentist
    approves most cosmetic follow-ups. Whatever is left can escalate."""
    rng = random.Random(f"desk|{seed}|{office['office_id']}|{day}")
    done = approved = 0
    rows = conn.execute(text("SELECT task_id, kind FROM desk_tasks WHERE status = 'open' ORDER BY task_id")).all()
    for task_id, kind in rows:
        if kind == "approval":
            if rng.random() < cfg["desk"]["approve"]:
                approved += int(bool(approve(conn, office, msg_settings, task_id, day)["sent"]))
        elif rng.random() < cfg["desk"]["resolve_per_day"]:
            done += int(desk.resolve(conn, task_id, day))
    return {"desk_done": done, "approved": approved}


def true_lift(conn: Connection, truth: dict[str, Row], cfg: Row, group: str) -> float:
    """The lift the holdout comparison should find for one group: the outreach group's chances with and without
    the outreach they actually got, in the same terms as the estimate."""
    rows = conn.execute(
        text(
            "SELECT t.patient_id, t.purpose, t.status, m.channel FROM outreach_targets t "
            "LEFT JOIN messages m USING (target_id) WHERE t.arm = 'outreach' AND t.lift_group = :g"
        ),
        {"g": group},
    ).all()
    base = with_ = 0.0
    n = 0
    for pid, purpose, status, channel in rows:
        if pid not in truth:
            continue
        ch = channel or ("desk_call" if status == "desk_call" else "")
        b, p = chances(truth[pid], purpose, group, ch, cfg)
        base, with_, n = base + b, with_ + p, n + 1
    base, with_ = base / n, with_ / n
    return (with_ - base) / (1 - base)
