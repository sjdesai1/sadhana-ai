"""Simulator scheduling pieces: appointment records and weather. The calendar itself lives in
`sadhana.office_calendar`, shared with the product; its names are re-exported here.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from datetime import date, timedelta

from sadhana.office_calendar import (  # noqa: F401  (re-exported for the simulator)
    DAYS,
    DOCTOR_ROLES,
    WINDOWS,
    Calendar,
    Row,
    add_months,
    age_at,
    hhmm,
    holiday_week,
    holidays,
    nth_weekday,
    to_min,
    works_on,
)


@dataclass
class Appointment:
    appt_id: str
    patient_id: str
    provider_id: str
    column: str  # hygiene, doctor, specialist
    date: str
    start: str
    end: str
    kind: str  # new_patient, recall_adult, recall_child, perio_maintenance, srp, treatment, followup, emergency
    codes: str
    booked_on: str
    status: str  # scheduled, completed, no_show, late_cancel, cancelled
    status_on: str
    rebook_of: str


@dataclass
class AppointmentTruth:
    appt_id: str
    cause: str  # why it did not happen: patient, weather, holiday_week, summer_friday, office_closed, left_practice


@dataclass
class Procedure:
    appt_id: str
    patient_id: str
    date: str
    provider_id: str
    code: str
    tooth: str
    plan_id: str  # the treatment plan item it completes; empty for cleanings, exams and x-rays


def weather(rng: random.Random, start: date, end: date, season: Row) -> tuple[set[date], set[date]]:
    """Bad-weather days (more no-shows) and storm days when the office closes."""
    bad: set[date] = set()
    closed: set[date] = set()
    n_closed = season["office_closed_storms_per_winter"]
    for y in range(start.year - 1, end.year + 1):
        winter = [date(y, 12, 1) + timedelta(days=i) for i in range(120)]
        summer = [date(y, 6, 1) + timedelta(days=i) for i in range(92)]
        picks = rng.sample(winter, season["bad_weather_days_per_winter"] + n_closed)
        closed |= set(picks[:n_closed])
        bad |= set(picks[n_closed:])
        bad |= set(rng.sample(summer, season["storm_days_per_summer"]))
    return bad, closed
