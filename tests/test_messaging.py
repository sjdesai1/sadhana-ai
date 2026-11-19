"""Messaging rules without a database: opt-out words, consent, caps, guardians and wording."""

import re
from datetime import date

import pytest

from sadhana.config import settings
from sadhana.messaging.policy import Policy, classify, load_settings
from sadhana.messaging.templates import TEMPLATES, render

S = load_settings(settings.configs_dir)
MON = date(2026, 11, 2)


@pytest.mark.parametrize(
    "body", ["STOP", "stop", " Stop. ", "STOPALL", "Unsubscribe", "cancel", "quit", "ALTO", "baja",
             "Please stop texting me", "do not text this number", "remove me from this list"],
)  # fmt: skip
def test_opt_out_words_are_honored(body) -> None:
    assert classify(body, S) == "opt_out"


@pytest.mark.parametrize(
    ("body", "action"),
    [("START", "opt_in"), ("unstop", "opt_in"), ("HELP", "help"), ("ayuda", "help"), ("Yes", "reply"),
     ("C", "reply"), ("Can I come Tuesday instead?", "reply"), ("Not right now, thanks", "reply")],
)  # fmt: skip
def test_other_replies(body, action) -> None:
    assert classify(body, S) == action


def _patient(pid, **kw):
    base = {"patient_id": pid, "guardian_id": None, "birth_date": date(1985, 5, 1), "language": "English",
            "phone_type": "cell", "sms_consent": True, "email_on_file": True, "marketing_consent": False}  # fmt: skip
    return {**base, **kw}


def test_channels_follow_consent_and_opt_outs() -> None:
    patients = {
        "A": _patient("A"),
        "B": _patient("B", sms_consent=False),
        "C": _patient("C", sms_consent=False, email_on_file=False),
        "D": _patient("D", sms_consent=False, email_on_file=False, phone_type="none"),
        "E": _patient("E"),
    }
    policy = Policy(S, patients, {("E", "sms")})
    assert policy.decide("A", "recall", MON)[1].channel == "sms"
    assert policy.decide("B", "recall", MON)[1].channel == "email"
    assert policy.decide("C", "recall", MON)[1].status == "desk_call"
    assert policy.decide("D", "recall", MON)[1].status == "blocked"
    # after a text STOP: no texts at all, and no outreach by email either, only visit confirmations
    assert policy.decide("E", "recall", MON)[1].status == "blocked"
    assert policy.decide("E", "treatment", MON)[1].status == "blocked"
    assert policy.decide("E", "confirm", MON)[1].channel == "email"


def test_cosmetic_needs_marketing_consent() -> None:
    policy = Policy(S, {"A": _patient("A"), "B": _patient("B", marketing_consent=True)}, set())
    assert policy.decide("A", "treatment", MON, "D9972")[1].reason == "cosmetic without marketing consent"
    assert policy.decide("B", "treatment", MON, "D9972")[1].status == "messaged"
    assert policy.decide("A", "treatment", MON, "D2740")[1].status == "messaged"


def test_caps_send_days_and_guardians() -> None:
    patients = {"G": _patient("G"), "K": _patient("K", guardian_id="G", birth_date=date(2016, 3, 1), sms_consent=False)}
    policy = Policy(S, patients, set())
    contact, d = policy.decide("K", "recall", MON)
    assert contact == "G" and d.channel == "sms"  # a child's message goes to the guardian, on the guardian's consent
    policy.remember("G", MON)
    assert policy.decide("G", "treatment", MON)[1].reason == "daily cap"
    policy.remember("G", date(2026, 11, 3))
    assert policy.decide("G", "treatment", date(2026, 11, 5))[1].reason == "weekly cap"
    assert policy.decide("G", "treatment", date(2026, 11, 10))[1].status == "messaged"
    assert policy.decide("G", "recall", date(2026, 11, 8))[1].reason == "outside send days or quiet hours"  # Sunday


def test_wording_is_care_follow_up_with_opt_out_and_no_clinical_detail() -> None:
    for key in ("recall", "slot_offer", "treatment", "confirm"):
        for lang in ("English", "Spanish", "Korean"):
            body = render(key, lang, "sms", office="Demo Office", day="Monday", time="9:00 AM", benefits_expiring=True)
            assert "STOP" in body or "ALTO" in body
            assert not re.search(r"\bD\d{4}\b", body)
            low = body.lower()
            assert not any(
                w in low for w in ("upsell", "deal", "sale", "discount", "limited time", "crown", "root canal")
            )
        email = render(key, "English", "email", office="Demo Office", day="Monday", time="9:00 AM", unsubscribe="u")
        assert "Unsubscribe" in email
    assert render("recall", "Spanish", office="X").startswith("Hola")
    assert render("recall", "Korean", office="X").startswith("Hi")  # falls back to English until reviewed
    assert set(TEMPLATES["recall"]) == {"en", "es"}
