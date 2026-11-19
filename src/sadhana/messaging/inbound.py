"""Inbound texts and email replies: opt-outs take effect before anything else is sent, with one confirmation."""

from __future__ import annotations

from datetime import date
from typing import Any

from sqlalchemy import Connection, insert, text

from sadhana.data.schema import inbound_messages, opt_outs
from sadhana.messaging.policy import classify
from sadhana.messaging.templates import render

Row = dict[str, Any]


def handle(
    conn: Connection,
    office: Row,
    settings: Row,
    contact_id: str,
    channel: str,
    body: str,
    received_on: date,
    language: str | None = None,
) -> Row:
    """Record one inbound message, apply opt-out or opt-in, and return the action and any automatic reply."""
    action = classify(body, settings)
    name = office.get("name", "Your dental office")
    auto = ""
    if action == "opt_out":
        conn.execute(
            text(
                "INSERT INTO opt_outs (office_id, contact_id, channel, opted_out_on, source) "
                "VALUES (:o, :c, :ch, :d, :s) ON CONFLICT (contact_id, channel) DO NOTHING"
            ),
            {"o": office["office_id"], "c": contact_id, "ch": channel, "d": received_on, "s": body[:200]},
        )
        auto = render("opt_out_ack", language, office=name) if channel == "sms" else ""
    elif action == "opt_in":
        conn.execute(opt_outs.delete().where(opt_outs.c.contact_id == contact_id, opt_outs.c.channel == channel))
        auto = render("opt_in_ack", language, office=name) if channel == "sms" else ""
    elif action == "help":
        auto = render("help", language, office=name)
    conn.execute(
        insert(inbound_messages).values(
            office_id=office["office_id"],
            contact_id=contact_id,
            channel=channel,
            received_on=received_on,
            body=body,
            action=action,
            auto_reply=auto,
        )
    )
    return {"action": action, "auto_reply": auto}
