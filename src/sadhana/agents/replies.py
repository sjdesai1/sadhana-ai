"""The reply agent: answers a patient's text inside the approved templates, or hands it to the front desk.

- "Yes" to a slot offer or refill: book it if it is still free; if not, say so and offer other times
- "Yes" to recall or treatment follow-up: offer the next two openings; "1" or "2" books one
- "C" to a confirmation: confirmed. "R" or "can I do Tuesday instead?": cancel the visit, offer the freed slot
  to backups right away (refill), and offer the patient two new times
- "No" or "not right now": a polite close
- A question or anything else: a holding reply, and a "needs reply" item for the front desk

Opt-out words are handled first (messaging.inbound); nobody who opted out of a channel gets a reply on it.
Bookings and cancellations wait in `pending_bookings` to be written to the PMS.
"""

from __future__ import annotations

import json
from datetime import date, time, timedelta
from typing import Any

from sqlalchemy import Connection, insert, text

from sadhana.agents import desk, refill
from sadhana.agents.outreach import friendly, load_patients, load_policy
from sadhana.agents.scheduling import Scheduler, _hhmm
from sadhana.data.schema import messages
from sadhana.messaging import inbound
from sadhana.messaging.intents import intent
from sadhana.messaging.templates import language_code, render
from sadhana.office_calendar import to_min

Row = dict[str, Any]


class ReplyDesk:
    """Everything replies need for one office and one day, loaded once."""

    def __init__(self, conn: Connection, office: Row, settings: Row, day: date) -> None:
        self.conn, self.office, self.s, self.day = conn, office, settings, day
        self.patients = load_patients(conn)
        self._sched: Scheduler | None = None
        self._policy = None
        self.counts: dict[str, int] = {}
        self.seq = 0

    @property
    def sched(self) -> Scheduler:
        if self._sched is None:
            self._sched = Scheduler(self.conn, self.office, self.day)
        return self._sched

    @property
    def policy(self):  # noqa: ANN201
        if self._policy is None:
            self._policy = load_policy(self.conn, self.s, self.patients, self.day)
        return self._policy

    def new_day(self, day: date) -> None:
        """Keep the calendar and patients; reload the rules (opt-outs and caps change day to day)."""
        self.day, self._policy = day, None

    def cancellation(self, appt_id: str) -> Row:
        """The PMS reports a cancelled visit (the doorbell): free the slot and offer it to backups right away."""
        slot = self.sched.cancel(appt_id, self.day, "pms", status="in_pms")
        if slot is None:
            return {"appt_id": appt_id, "freed": False, "offered": []}
        offered = refill.offer(self.conn, self.office, self.s, self.sched, self.policy, self.patients, slot, self.day)
        desk.add(self.conn, self.office["office_id"], self.s, "fyi", slot["patient_id"],
                 f"Visit {appt_id} cancelled; slot offered to {len(offered)} backups", self.day)  # fmt: skip
        if not offered:  # outside send hours, or nobody the rules allow: a person fills it
            desk.add(self.conn, self.office["office_id"], self.s, "call", slot["patient_id"],
                     f"Open slot {slot['date']} {slot['start']} with {slot['provider_id']}: call the waitlist",
                     self.day, ref_id=appt_id)  # fmt: skip
        self._count("cancellations")
        self.counts["refill_offers"] = self.counts.get("refill_offers", 0) + len(offered)
        return {"appt_id": appt_id, "freed": True, "offered": offered}

    def _count(self, key: str) -> None:
        self.counts[key] = self.counts.get(key, 0) + 1

    # helpers

    def _when(self, d: Any, start: Any, lang: str | None) -> tuple[str, str]:
        d = date.fromisoformat(d) if isinstance(d, str) else d
        return friendly(d, _hhmm(start), lang)

    def _send(self, contact: str, patient: str, channel: str, key: str, lang: str | None, **fields: Any) -> str:
        body = render(key, lang, channel, office=self.office.get("name", "your dental office"), **fields)
        self.conn.execute(
            insert(messages).values(
                office_id=self.office["office_id"], message_id=self._message_id(contact), kind="reply",
                patient_id=patient, contact_id=contact, channel=channel, language=lang, body=body,
                sent_on=self.day, sent_at=time.fromisoformat(self.s["send_at"]), status="sent",
            )
        )  # fmt: skip
        return body

    def _message_id(self, contact: str) -> str:
        """Sorts in the order replies were sent, and is the same on every run of the same simulation."""
        n = self.conn.execute(
            text("SELECT count(*) FROM messages WHERE kind = 'reply' AND contact_id = :c AND sent_on = :d"),
            {"c": contact, "d": self.day},
        ).scalar()
        self.seq += 1
        return f"r-{self.day:%Y%m%d}-{self.seq:06d}-{contact}-{n}"

    def _offer_times(self, contact: str, patient: str, visit: Row, plan_id: str, key: str) -> tuple[str, Row]:
        opts = self.sched.openings(patient, visit, self.s["booking_options"], self.day + timedelta(days=1))
        if not opts:
            desk.add(self.conn, self.office["office_id"], self.s, "call", patient,
                     "Patient wants to book; no openings to offer by text", self.day, contact)  # fmt: skip
            return "no_openings", {}
        self.conn.execute(
            text(
                "INSERT INTO conversations (office_id, contact_id, patient_id, state, options, code, plan_id, "
                "updated_on) VALUES (:o, :c, :p, 'offered', CAST(:opts AS json), :code, :plan, :d) "
                "ON CONFLICT (contact_id) DO UPDATE SET patient_id = :p, state = 'offered', options = "
                "CAST(:opts AS json), code = :code, plan_id = :plan, updated_on = :d"
            ),
            {"o": self.office["office_id"], "c": contact, "p": patient, "opts": _json(opts), "code": visit["code"],
             "plan": plan_id or None, "d": self.day},
        )  # fmt: skip
        lang = self.patients[contact].get("language")
        at = " a las " if language_code(lang) == "es" else " at "
        labels = [at.join(self._when(o["date"], o["start"], lang)) for o in opts]
        if len(opts) == 1:
            return ("offer_one" if key == "offer_times" else key), {"opt1": labels[0], "opt2": labels[0]}
        return key, {"opt1": labels[0], "opt2": labels[1]}

    def _book(self, contact: str, patient: str, opt: Row, source: str, plan_id: str) -> tuple[str, Row]:
        d = date.fromisoformat(opt["date"]) if isinstance(opt["date"], str) else opt["date"]
        start = _hhmm(opt["start"])
        if not self.sched.free(d, opt["provider_id"], to_min(start), opt["minutes"], opt["column"]):
            return "", {}
        self.sched.book(patient, opt, source, self.day, plan_id)
        self.conn.execute(text("UPDATE conversations SET state = 'idle' WHERE contact_id = :c"), {"c": contact})
        lang = self.patients[contact].get("language")
        day_s, time_s = self._when(d, start, lang)
        desk.add(self.conn, self.office["office_id"], self.s, "fyi", patient,
                 f"Booked by text: {opt.get('code') or 'visit'} {d} {start} with {opt['provider_id']}", self.day,
                 contact)  # fmt: skip
        self._count("booked")
        return "booked", {"day": day_s, "time": time_s}

    def _last(self, contact: str) -> Row | None:
        """The latest outreach or refill offer this contact got, with what it was about."""
        r = (
            self.conn.execute(
                text(
                    "SELECT m.kind, m.ref_id, m.patient_id, t.purpose, t.ref_id AS target_ref, t.code "
                    "FROM messages m LEFT JOIN outreach_targets t USING (target_id) "
                    "WHERE m.contact_id = :c AND m.kind IN ('outreach', 'refill') "
                    "ORDER BY m.sent_on DESC, m.message_id DESC LIMIT 1"
                ),
                {"c": contact},
            )
            .mappings()
            .first()
        )
        return dict(r) if r else None

    # the reply

    def receive(self, contact: str, channel: str, body: str) -> Row:
        lang = self.patients.get(contact, {}).get("language")
        what = intent(body, self.s)
        res = inbound.handle(self.conn, self.office, self.s, contact, channel, body, self.day, lang, what)
        if res["action"] != "reply":
            if res["action"] == "opt_out":
                self.policy.opt_outs.add((contact, channel))
            return {**res, "intent": what}
        if (contact, channel) in self.policy.opt_outs:
            desk.add(self.conn, self.office["office_id"], self.s, "needs_reply", contact,
                     f"Message from someone who opted out of {channel}: {body[:120]}", self.day, contact)  # fmt: skip
            return {**res, "intent": what, "auto_reply": ""}
        key, fields = self._dispatch(contact, what, body)
        reply = self._send(contact, (self._last(contact) or {}).get("patient_id") or contact, channel, key, lang,
                           **fields)  # fmt: skip
        self._count(what)
        return {"action": "reply", "intent": what, "auto_reply": reply}

    def _dispatch(self, contact: str, what: str, body: str) -> tuple[str, Row]:
        oid = self.office["office_id"]
        last = self._last(contact)
        conv = (
            self.conn.execute(
                text("SELECT patient_id, state, options, code, plan_id FROM conversations WHERE contact_id = :c"),
                {"c": contact},
            )
            .mappings()
            .first()
        )
        if what == "choose" and conv and conv["state"] == "offered":
            n = int(body.strip(".!) ")) - 1
            opts = conv["options"] or []
            if 0 <= n < len(opts):
                key, fields = self._book(contact, conv["patient_id"], opts[n], "reply", conv["plan_id"] or "")
                if key:
                    return key, fields
                visit = self.sched.visit(conv["patient_id"], conv["code"] or "")
                return self._offer_times(contact, conv["patient_id"], visit, conv["plan_id"] or "", "taken")
        if last is None or what in ("question", "other") or (what == "choose"):
            desk.add(self.conn, oid, self.s, "needs_reply", contact, f"Patient wrote: {body[:160]}", self.day,
                     contact)  # fmt: skip
            return "holding", {}
        patient = last["patient_id"]
        purpose = "refill" if last["kind"] == "refill" else last["purpose"]
        if what == "decline":
            desk.add(self.conn, oid, self.s, "fyi", patient, f"Declined {purpose}", self.day, contact)
            return "declined", {}
        if what in ("accept", "confirm") and purpose == "confirm":
            a = self.sched.appts.get(last["target_ref"])
            desk.add(self.conn, oid, self.s, "fyi", patient, f"Confirmed visit {last['target_ref']}", self.day, contact)
            self._count("confirmed")
            lang = self.patients[contact].get("language")
            if a is None:
                return "holding", {}
            day_s, time_s = self._when(a["date"], a["start"], lang)
            return "confirmed", {"day": day_s, "time": time_s}
        if what == "reschedule" and purpose == "confirm":
            slot = self.sched.cancel(last["target_ref"], self.day, "reschedule")
            if slot is None:
                desk.add(self.conn, oid, self.s, "needs_reply", patient, "Wants to reschedule; visit not found",
                         self.day, contact)  # fmt: skip
                return "holding", {}
            self._count("rescheduled")
            desk.add(self.conn, oid, self.s, "fyi", patient, f"Cancelled {slot['appt_id']} to reschedule; slot "
                     "offered to backups", self.day, contact)  # fmt: skip
            offered = refill.offer(self.conn, self.office, self.s, self.sched, self.policy, self.patients, slot,
                                   self.day)  # fmt: skip
            self.counts["refill_offers"] = self.counts.get("refill_offers", 0) + len(offered)
            same_column = {p["id"] for p in self.sched.cal.columns[slot["column"]]}
            visit = {"code": slot["code"], "minutes": slot["minutes"], "column": slot["column"], "allowed": same_column}
            return self._offer_times(contact, patient, visit, "", "reschedule_times")
        if what == "accept" and purpose in ("refill", "slot_offer"):
            if purpose == "refill":
                o = self.conn.execute(text("SELECT * FROM refill_offers WHERE offer_id = :i"),
                                      {"i": last["ref_id"]}).mappings().one()  # fmt: skip
                opt = {k: o[k] for k in ("date", "start", "minutes", "provider_id", "column", "code")}
                plan_id = o["plan_id"] or ""
                key, fields = ("", {}) if o["status"] != "offered" else self._book(contact, patient, opt, "refill",
                                                                                   plan_id)  # fmt: skip
                if key:
                    self.conn.execute(text("UPDATE refill_offers SET status = 'won' WHERE offer_id = :i"),
                                      {"i": o["offer_id"]})  # fmt: skip
                    self.conn.execute(
                        text("UPDATE refill_offers SET status = 'taken' WHERE slot_key = :k AND offer_id <> :i "
                             "AND status = 'offered'"), {"k": o["slot_key"], "i": o["offer_id"]})  # fmt: skip
                    self._count("refilled")
                    return key, fields
                self.conn.execute(text("UPDATE refill_offers SET status = 'taken' WHERE offer_id = :i"),
                                  {"i": o["offer_id"]})  # fmt: skip
                return self._offer_times(contact, patient, self.sched.visit(patient, o["code"] or ""), plan_id, "taken")
            row = (
                self.conn.execute(
                    text(
                        'SELECT date, start, minutes, provider_id, "column", code, plan_id FROM slot_plan WHERE id = :i'
                    ),
                    {"i": int(str(last["target_ref"]).removeprefix("slot"))},
                )
                .mappings()
                .first()
            )
            if row:
                key, fields = self._book(contact, patient, dict(row), "slot_offer", row["plan_id"] or "")
                if key:
                    return key, fields
            visit = self.sched.visit(patient, last["code"] or "")
            return self._offer_times(contact, patient, visit, (row or {}).get("plan_id") or "", "taken")
        if what == "accept" and purpose in ("recall", "treatment"):
            plan_id = last["target_ref"] if purpose == "treatment" else ""
            return self._offer_times(contact, patient, self.sched.visit(patient, last["code"] or ""), plan_id,
                                     "offer_times")  # fmt: skip
        desk.add(self.conn, oid, self.s, "needs_reply", patient, f"Patient wrote: {body[:160]}", self.day, contact)
        return "holding", {}


def _json(v: Any) -> str:
    return json.dumps(v, default=str)
