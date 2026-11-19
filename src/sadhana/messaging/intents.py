"""What a reply means. Rules today; a language model can sit behind the same function later, inside the
approved templates and the office's budget (decision 0007).

Opt-out words win over everything, so "CANCEL" stops texts rather than cancelling a visit; that is why
confirmations ask for R to reschedule.
"""

from __future__ import annotations

import re
from typing import Any

from sadhana.messaging.policy import classify

Row = dict[str, Any]


def intent(body: str, settings: Row) -> str:
    """opt_out, opt_in, help, choose, confirm, reschedule, decline, accept, question or other."""
    action = classify(body, settings)
    if action != "reply":
        return action
    k = settings["intents"]
    text = body.strip()
    low = text.lower()
    first = re.sub(r"[^\wÍí']", "", text.split()[0]).upper() if text else ""
    if re.fullmatch(r"[1-9]", text.strip(".!) ")):
        return "choose"
    if first in k["confirm"]:
        return "confirm"
    if first in k["reschedule"] or any(p in low for p in k["reschedule_phrases"]):
        return "reschedule"
    if first in k["decline"] or any(p in low for p in k["decline_phrases"]):
        return "decline"
    if first in k["accept"] or any(p in low for p in k["accept_phrases"]):
        return "accept"
    if "?" in text or low.split()[0] in k["question_starts"]:
        return "question"
    return "other"
