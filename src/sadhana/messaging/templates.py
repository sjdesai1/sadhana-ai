"""Message wording. Short, plain, follow-up language: never sales language, never procedure names or codes in a
text (minimum necessary), always how to opt out. Language only picks the wording.

English and Spanish are written; other languages fall back to English until reviewed translations are added.
"""

from __future__ import annotations

from typing import Any

STOP_SMS = {"en": "Reply STOP to opt out.", "es": "Responda ALTO para no recibir más mensajes."}
STOP_EMAIL = {"en": "Unsubscribe: {unsubscribe}", "es": "Cancelar suscripción: {unsubscribe}"}

TEMPLATES: dict[str, dict[str, str]] = {
    "recall": {
        "en": "Hi, this is {office}. You're due for your cleaning and checkup. Reply YES and we'll find a time.",
        "es": "Hola, le escribe {office}. Ya le toca su limpieza y revisión. Responda SÍ y le buscamos una cita.",
    },
    "slot_offer": {
        "en": "Hi, this is {office}. An opening came up on {day} at {time}. Reply YES to take it.",
        "es": "Hola, le escribe {office}. Se abrió un espacio el {day} a las {time}. Responda SÍ para tomarlo.",
    },
    "treatment": {
        "en": "Hi, this is {office}, following up on the care your dentist recommended at your last visit. "
        "Reply YES and we'll set up a time.",
        "es": "Hola, le escribe {office} para dar seguimiento al tratamiento que su dentista le recomendó en su "
        "última visita. Responda SÍ y le damos una cita.",
    },
    "confirm": {
        "en": "Hi, this is {office}. Please confirm your visit on {day} at {time}: reply C to confirm or R to "
        "reschedule.",
        "es": "Hola, le escribe {office}. Confirme su cita del {day} a las {time}: responda C para confirmar o R "
        "para cambiarla.",
    },
    "benefits_note": {
        "en": " Your insurance benefits for this year reset Dec 31.",
        "es": " Sus beneficios de seguro de este año se reinician el 31 de diciembre.",
    },
    "opt_out_ack": {
        "en": "{office}: You're unsubscribed and won't get more texts. Reply START to resubscribe.",
        "es": "{office}: Ya no recibirá más mensajes. Responda START para volver a suscribirse.",
    },
    "opt_in_ack": {
        "en": "{office}: You're subscribed to texts again. Reply STOP to opt out.",
        "es": "{office}: Volverá a recibir mensajes. Responda ALTO para no recibir más.",
    },
    "help": {
        "en": "{office}: Reply STOP to opt out. For anything else, call the office.",
        "es": "{office}: Responda ALTO para no recibir más mensajes. Para lo demás, llame a la oficina.",
    },
}

LANGUAGE_CODES = {"English": "en", "Spanish": "es"}


def language_code(language: str | None) -> str:
    return LANGUAGE_CODES.get(language or "", "en")


def render(key: str, language: str | None, channel: str = "sms", **fields: Any) -> str:
    """The message for one purpose in the patient's language, with the opt-out line for the channel."""
    code = language_code(language)
    body = TEMPLATES[key].get(code, TEMPLATES[key]["en"]).format(**fields)
    if fields.get("benefits_expiring") and key in ("treatment", "slot_offer"):
        body += TEMPLATES["benefits_note"][code]
    if key in ("opt_out_ack", "opt_in_ack", "help"):
        return body
    if channel == "sms":
        return f"{body} {STOP_SMS[code]}"
    return f"{body}\n\n{STOP_EMAIL[code].format(unsubscribe=fields.get('unsubscribe', '{unsubscribe_link}'))}"
