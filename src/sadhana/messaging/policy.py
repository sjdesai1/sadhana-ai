"""Who may be messaged, on which channel, and how often.

- STOP on a text stops texts at once, for every purpose. Email after a text STOP only for what settings allow
  (visit confirmations by default), never outreach
- Unsubscribing from email stops email
- Texts need text consent and a cell phone; email needs an address on file
- Cosmetic follow-up needs marketing consent
- A child's messages go to the guardian, using the guardian's consent
- Caps per contact per day and per week; nothing outside quiet hours or on a non-send day
- Nobody reachable: the front desk gets a call task, if there is a phone number
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, time
from pathlib import Path
from typing import Any

from sadhana.office_calendar import DAYS, age_at

Row = dict[str, Any]


def load_settings(configs_dir: Path) -> Row:
    return json.loads((configs_dir / "messaging.json").read_text())


@dataclass
class Decision:
    channel: str  # sms, email, desk_call, or none
    status: str  # messaged, desk_call, blocked
    reason: str = ""


class Policy:
    def __init__(self, settings: Row, patients: dict[str, Row], opt_outs: set[tuple[str, str]]) -> None:
        self.s, self.patients, self.opt_outs = settings, patients, opt_outs
        self.sent: dict[str, list[date]] = {}  # contact -> days messaged

    def contact_for(self, patient_id: str, today: date) -> str:
        """A child's messages go to the guardian."""
        p = self.patients[patient_id]
        birth = p.get("birth_date")
        if p.get("guardian_id") and birth is not None and age_at(birth, today) < 18:
            return p["guardian_id"] if p["guardian_id"] in self.patients else patient_id
        return patient_id

    def remember(self, contact_id: str, day: date) -> None:
        self.sent.setdefault(contact_id, []).append(day)

    def can_send_on(self, day: date, at: time) -> bool:
        q = self.s["quiet_hours"]
        return DAYS[day.weekday()] in self.s["send_days"] and time.fromisoformat(q["start"]) <= at < time.fromisoformat(
            q["end"]
        )

    def _capped(self, contact_id: str, day: date) -> str:
        days = self.sent.get(contact_id, [])
        caps = self.s["caps"]
        if sum(1 for d in days if d == day) >= caps["per_day"]:
            return "daily cap"
        if sum(1 for d in days if 0 <= (day - d).days < 7) >= caps["per_week"]:
            return "weekly cap"
        return ""

    def decide(self, patient_id: str, purpose: str, day: date, code: str = "") -> tuple[str, Decision]:
        """(contact, decision) for one message about one patient."""
        contact_id = self.contact_for(patient_id, day)
        c = self.patients[contact_id]
        if purpose not in self.s["care_purposes"]:
            return contact_id, Decision("none", "blocked", f"unknown purpose {purpose}")
        if code in self.s["cosmetic_codes"] and not c.get("marketing_consent"):
            return contact_id, Decision("none", "blocked", "cosmetic without marketing consent")
        if not self.can_send_on(day, time.fromisoformat(self.s["send_at"])):
            return contact_id, Decision("none", "blocked", "outside send days or quiet hours")
        capped = self._capped(contact_id, day)
        if capped:
            return contact_id, Decision("none", "blocked", capped)
        sms_stopped = (contact_id, "sms") in self.opt_outs
        if c.get("phone_type") == "cell" and c.get("sms_consent") and not sms_stopped:
            return contact_id, Decision("sms", "messaged")
        after_stop = self.s["email_after_sms_stop"]
        email_ok = c.get("email_on_file") and (contact_id, "email") not in self.opt_outs
        if email_ok and (not sms_stopped or (after_stop["allowed"] and purpose in after_stop["purposes"])):
            return contact_id, Decision("email", "messaged")
        if sms_stopped:
            return contact_id, Decision("none", "blocked", "opted out")
        if c.get("phone_type") in ("cell", "landline"):
            return contact_id, Decision("desk_call", "desk_call", "no text or email consent")
        return contact_id, Decision("none", "blocked", "no way to reach")


def classify(body: str, settings: Row) -> str:
    """opt_out, opt_in, help or reply, for an inbound text or email reply."""
    k = settings["keywords"]
    text = body.strip()
    first = text.split()[0].strip(".,!").upper() if text else ""
    low = text.lower()
    if first in k["opt_out"] or any(p in low for p in k["opt_out_phrases"]):
        return "opt_out"
    if first in k["opt_in"]:
        return "opt_in"
    if first in k["help"]:
        return "help"
    return "reply"
