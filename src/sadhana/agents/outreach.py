"""The outreach agent: each send day, decide who hears from the office, about what, and on which channel.

Order of work: confirmations for booked visits at risk of being missed, then the slot plan's offers (first day
only, while the slots are open), then treatment follow-up by expected value, then overdue cleanings, least likely
to come back on their own first. Daily limits keep the front desk from being flooded with replies.

A random holdout group (fixed per patient) gets nothing, so the effect of outreach is measured, not assumed.
Arms are assigned before consent and caps are checked, and results are compared by arm (intention to treat),
so the measured lift is what the office would really get.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import date, time, timedelta
from typing import Any

import pandas as pd
from sqlalchemy import Connection, insert, text

from sadhana.agents import desk
from sadhana.data.schema import messages, outreach_targets
from sadhana.messaging.policy import Policy
from sadhana.messaging.templates import language_code, render

Row = dict[str, Any]


@dataclass
class Target:
    target_id: str
    patient_id: str
    purpose: str
    lift_group: str
    ref_id: str
    code: str
    targeted_on: date
    arm: str = "outreach"
    status: str = ""
    reason: str = ""
    channel: str = ""
    contact_id: str = ""
    segment: str = ""


def in_holdout(office_id: str, patient_id: str, lift_group: str, share: float) -> bool:
    """Fixed per patient and group, so the same people stay in the holdout from day to day."""
    h = hashlib.sha256(f"{office_id}|{patient_id}|{lift_group}".encode()).hexdigest()
    return int(h[:8], 16) / 0xFFFFFFFF < share


NAMES = {
    "en": (
        ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"],
        ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"],
    ),
    "es": (
        ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"],
        ["ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sep", "oct", "nov", "dic"],
    ),
}


def _friendly(d: date, start: str, language: str | None = None) -> tuple[str, str]:
    """Day and time the way the patient would say them: "Tuesday Nov 3", "9:40 AM"; "martes 3 nov", "9:40"."""
    h, m = (int(x) for x in start[:5].split(":"))
    code = language_code(language)
    days, months = NAMES[code]
    if code == "es":
        return f"{days[d.weekday()]} {d.day} {months[d.month - 1]}", f"{h}:{m:02d}"
    return (
        f"{days[d.weekday()]} {months[d.month - 1]} {d.day}",
        f"{(h - 1) % 12 + 1}:{m:02d} {'AM' if h < 12 else 'PM'}",
    )


friendly = _friendly


def load_patients(conn: Connection) -> dict[str, Row]:
    """What the rules need about each patient and contact."""
    rows = conn.execute(
        text(
            "SELECT patient_id, guardian_id, birth_date, language, phone_type, sms_consent, email_on_file, "
            "marketing_consent FROM patients"
        )
    ).mappings()
    return {r["patient_id"]: dict(r) for r in rows}


def load_policy(conn: Connection, settings: Row, patients: dict[str, Row], day: date) -> Policy:
    """The rules with today's opt-outs and the last week's outreach (replies in a conversation do not count)."""
    opt = {(c, ch) for c, ch in conn.execute(text("SELECT contact_id, channel FROM opt_outs")).all()}
    policy = Policy(settings, patients, opt)
    for contact, sent_on in conn.execute(
        text("SELECT contact_id, sent_on FROM messages WHERE sent_on > :d AND coalesce(kind, '') <> 'reply'"),
        {"d": day - timedelta(days=7)},
    ):
        policy.remember(contact, sent_on)
    return policy


class OutreachAgent:
    def __init__(self, conn: Connection, office: Row, settings: Row, today: date) -> None:
        self.conn, self.office, self.s, self.today = conn, office, settings, today
        q = lambda sql, **p: pd.DataFrame(conn.execute(text(sql), p).mappings().all())  # noqa: E731
        self.patients = load_patients(conn)
        self.status = {
            r["patient_id"]: dict(r)
            for r in conn.execute(
                text("SELECT patient_id, first_visit, last_visit, completed_visits FROM patient_status")
            ).mappings()
        }
        self.slot_plan = q("SELECT id, date, start, patient_id, plan_id, code FROM slot_plan ORDER BY date, start, id")
        self.at_risk = q(
            "SELECT appt_id, patient_id, date, start FROM at_risk_visits ORDER BY value_at_risk DESC, appt_id"
        )
        self.next_best = q(
            "SELECT patient_id, plan_id, code, expected_value, benefits_this_year FROM next_best "
            "ORDER BY expected_value DESC, patient_id"
        )
        self.recall = q(
            "SELECT patient_id, score FROM scores WHERE model = 'recall_self_book' ORDER BY score, patient_id"
        )
        self.first_day = None

    def _history(self, day: date) -> tuple[Policy, set[tuple[str, str]]]:
        policy = load_policy(self.conn, self.s, self.patients, day)
        since = day - timedelta(days=self.s["caps"]["retarget_after_days"])
        recent = {
            (p, g)
            for p, g in self.conn.execute(
                text("SELECT patient_id, lift_group FROM outreach_targets WHERE targeted_on > :d"), {"d": since}
            )
        }
        done = {r[0] for r in self.conn.execute(text("SELECT patient_id FROM outreach_results WHERE success"))}
        done |= {
            r[0]
            for r in self.conn.execute(
                text("SELECT patient_id FROM pending_bookings WHERE kind = 'book' AND date > :d"),
                {"d": day - timedelta(days=180)},  # booked by the agents: coming up, or seen in the last six months
            )
        }
        return policy, recent | {(p, g) for p in done for g in ("recall", "treatment")}

    def segment(self, patient_id: str, day: date) -> str:
        """New (first visit within a year, or two visits or fewer), lapsed (no visit in 18 months) or established."""
        st = self.status.get(patient_id)
        if st is None or st["last_visit"] is None or (day - st["last_visit"]).days > 548:
            return "lapsed"
        if (st["completed_visits"] or 0) <= 2 or (st["first_visit"] and (day - st["first_visit"]).days <= 365):
            return "new"
        return "established"

    def _candidates(self, day: date, skip: set[tuple[str, str]]) -> list[Target]:
        out: list[Target] = []
        seen: set[str] = set()

        def add(pid: str, purpose: str, group: str, ref: str, code: str = "") -> None:
            if pid in seen or (pid, group) in skip or pid not in self.patients:
                return
            seen.add(pid)
            t = Target(f"{day:%Y%m%d}-{purpose}-{ref}", pid, purpose, group, ref, code, day)
            t.segment = self.segment(pid, day)
            out.append(t)

        if self.first_day is None:
            self.first_day = day
        if day == self.first_day:
            for r in self.at_risk.itertuples() if len(self.at_risk) else []:
                if r.date > day:
                    add(r.patient_id, "confirm", "confirm", r.appt_id)
            for r in self.slot_plan.itertuples() if len(self.slot_plan) else []:
                if r.date > day:
                    add(r.patient_id, "slot_offer", "treatment" if r.plan_id else "recall", f"slot{r.id}", r.code)
        limit = self.s["daily_limit"]
        n = 0
        for r in self.next_best.itertuples() if len(self.next_best) else []:
            if n >= limit["treatment"]:
                break
            before = len(out)
            add(r.patient_id, "treatment", "treatment", r.plan_id, r.code)
            n += len(out) - before
        n = 0
        for r in self.recall.itertuples() if len(self.recall) else []:
            if n >= limit["recall"]:
                break
            before = len(out)
            add(r.patient_id, "recall", "recall", f"recall-{r.patient_id}")
            n += len(out) - before
        return out

    def _body(self, t: Target, channel: str) -> str:
        name = self.office.get("name", "your dental office")
        lang = self.patients[t.contact_id].get("language")
        fields: Row = {"office": name, "unsubscribe": f"https://example.invalid/u/{t.contact_id}"}
        if t.purpose == "slot_offer":
            r = self.slot_plan[self.slot_plan["id"].astype(str) == t.ref_id.removeprefix("slot")].iloc[0]
            fields["day"], fields["time"] = _friendly(r["date"], str(r["start"]), lang)
        if t.purpose == "confirm":
            r = self.at_risk[self.at_risk["appt_id"] == t.ref_id].iloc[0]
            fields["day"], fields["time"] = _friendly(r["date"], str(r["start"]), lang)
        if t.purpose == "treatment":
            nb = self.next_best[self.next_best["patient_id"] == t.patient_id]
            fields["benefits_expiring"] = bool(len(nb) and float(nb.iloc[0]["benefits_this_year"] or 0) > 0)
        return render(t.purpose, lang, channel, **fields)

    def message_row(self, t: Target, channel: str, day: date) -> Row:
        return {
            "office_id": self.office["office_id"],
            "message_id": f"m-{t.target_id}",
            "target_id": t.target_id,
            "kind": "outreach",
            "patient_id": t.patient_id,
            "contact_id": t.contact_id,
            "channel": channel,
            "language": self.patients[t.contact_id].get("language"),
            "body": self._body(t, channel),
            "sent_on": day,
            "sent_at": time.fromisoformat(self.s["send_at"]),
            "status": "sent",
        }

    def run_day(self, day: date) -> list[Target]:
        """Pick today's targets, assign arms, check the rules, and send through the (simulated) sender. Front desk
        calls and approvals go to the desk queue."""
        policy, skip = self._history(day)
        targets = self._candidates(day, skip)
        office_id = self.office["office_id"]
        share = self.s["holdout_share"]
        approver = self.s["approval_needed"]["cosmetic"]
        sent_rows, target_rows = [], []
        for t in targets:
            if in_holdout(office_id, t.patient_id, t.lift_group, share):
                t.arm, t.status, t.reason = "holdout", "held_out", "holdout group"
            else:
                t.contact_id, d = policy.decide(t.patient_id, t.purpose, day, t.code)
                t.status, t.reason, t.channel = d.status, d.reason, d.channel
                if d.status == "messaged" and t.code in self.s["cosmetic_codes"]:
                    t.status, t.reason = "needs_approval", f"cosmetic follow-up waits for the {approver}"
                    desk.add(self.conn, office_id, self.s, "approval", t.patient_id,
                             "Approve cosmetic follow-up text", day, t.contact_id, t.target_id, approver)  # fmt: skip
                elif d.status == "messaged":
                    policy.remember(t.contact_id, day)
                    sent_rows.append(self.message_row(t, d.channel, day))
                elif d.status == "desk_call":
                    desk.add(self.conn, office_id, self.s, "call", t.patient_id,
                             f"Call about {t.purpose.replace('_', ' ')} (no text or email consent)", day,
                             t.contact_id, t.target_id)  # fmt: skip
            target_rows.append(
                {
                    "office_id": office_id,
                    **{
                        k: getattr(t, k)
                        for k in ("target_id", "patient_id", "purpose", "lift_group", "ref_id", "segment")
                    },  # fmt: skip
                    "code": t.code,
                    "targeted_on": day,
                    "arm": t.arm,
                    "status": t.status,
                    "reason": t.reason,
                }
            )
        if target_rows:
            self.conn.execute(insert(outreach_targets), target_rows)
        if sent_rows:
            self.conn.execute(insert(messages), sent_rows)
        return targets


def approve(conn: Connection, office: Row, settings: Row, task_id: int, day: date) -> Row:
    """A person approves a waiting message: the rules are checked again (opt-outs and caps may have changed),
    then it is sent."""
    task = conn.execute(
        text("SELECT ref_id FROM desk_tasks WHERE task_id = :i AND kind = 'approval' AND status = 'open'"),
        {"i": task_id},
    ).first()
    if task is None:
        return {"sent": False, "reason": "no open approval with that id"}
    r = conn.execute(text("SELECT * FROM outreach_targets WHERE target_id = :t"), {"t": task.ref_id}).mappings().one()
    agent = OutreachAgent(conn, office, settings, day)
    policy = load_policy(conn, settings, agent.patients, day)
    t = Target(r["target_id"], r["patient_id"], r["purpose"], r["lift_group"], r["ref_id"], r["code"] or "",
               r["targeted_on"], r["arm"])  # fmt: skip
    t.contact_id, d = policy.decide(t.patient_id, t.purpose, day, t.code)
    desk.resolve(conn, task_id, day)
    if d.status != "messaged":
        conn.execute(text("UPDATE outreach_targets SET status = 'blocked', reason = :r WHERE target_id = :t"),
                     {"r": d.reason, "t": t.target_id})  # fmt: skip
        return {"sent": False, "reason": d.reason}
    conn.execute(insert(messages), [agent.message_row(t, d.channel, day)])
    conn.execute(text("UPDATE outreach_targets SET status = 'messaged', reason = 'approved' WHERE target_id = :t"),
                 {"t": t.target_id})  # fmt: skip
    return {"sent": True, "channel": d.channel}
