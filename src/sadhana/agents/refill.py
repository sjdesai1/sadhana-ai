"""Real-time refill: when a visit is cancelled, offer the slot right away to the best few backups at once; the
first yes gets it, everyone after hears it was taken and gets other times.

Backups are the planner's candidates (overdue cleanings, open diagnosed treatment), valued for this exact slot the
same way the slot plan values them: chance they book x chance they show x margin with this provider.
"""

from __future__ import annotations

from datetime import date, time, timedelta
from typing import Any

from sqlalchemy import Connection, insert

from sadhana.agents.outreach import friendly
from sadhana.agents.scheduling import Scheduler, _hhmm
from sadhana.data.schema import messages, refill_offers
from sadhana.messaging.policy import Policy
from sadhana.messaging.templates import render
from sadhana.office_calendar import to_min

Row = dict[str, Any]


def offer(
    conn: Connection, office: Row, settings: Row, sched: Scheduler, policy: Policy, patients: dict[str, Row],
    slot: Row, day: date,
) -> list[str]:  # fmt: skip
    """Offer a freed slot to the best backups the rules allow; returns who was offered it."""
    cfg = settings["refill"]
    slot_date = slot["date"]
    if slot_date < day + timedelta(days=cfg["min_lead_days"]):
        return []
    block = {"date": slot_date, "provider_id": slot["provider_id"], "column": slot["column"],
             "start": to_min(_hhmm(slot["start"])), "minutes": slot["minutes"]}  # fmt: skip
    ranked = sorted(
        (
            (sched.planner._value(block, c)[0], c)
            for c in sched.candidates()
            if c["patient_id"] != slot.get("patient_id")
        ),
        key=lambda x: -x[0],
    )
    key = f"{slot['provider_id']}-{slot_date}-{_hhmm(slot['start'])}"
    offered: list[str] = []
    for value, c in ranked:
        if value <= 0 or len(offered) >= cfg["offers_at_once"]:
            break
        if c["patient_id"] in offered:
            continue
        contact, d = policy.decide(c["patient_id"], "slot_offer", day, c["code"])
        if d.status != "messaged" or c["code"] in settings["cosmetic_codes"]:
            continue
        policy.remember(contact, day)
        offer_id = f"o-{key}-{c['patient_id']}"  # one offer per patient per slot
        length = sched.cal.duration(slot["provider_id"], c["minutes"])
        conn.execute(
            insert(refill_offers).values(
                office_id=office["office_id"], offer_id=offer_id, slot_key=key, patient_id=c["patient_id"],
                contact_id=contact, date=slot_date, start=time.fromisoformat(_hhmm(slot["start"])), minutes=length,
                provider_id=slot["provider_id"], column=slot["column"], code=c["code"], plan_id=c["plan_id"] or None,
                offered_on=day, status="offered",
            )
        )  # fmt: skip
        lang = patients[contact].get("language")
        day_s, time_s = friendly(slot_date, _hhmm(slot["start"]), lang)
        conn.execute(
            insert(messages).values(
                office_id=office["office_id"], message_id=f"m-{offer_id}", kind="refill", ref_id=offer_id,
                patient_id=c["patient_id"], contact_id=contact, channel=d.channel, language=lang,
                body=render("refill", lang, d.channel, office=office.get("name", "your dental office"), day=day_s,
                            time=time_s, unsubscribe=f"https://example.invalid/u/{contact}"),
                sent_on=day, sent_at=time.fromisoformat(settings["send_at"]), status="sent",
            )
        )  # fmt: skip
        offered.append(c["patient_id"])
    return offered
