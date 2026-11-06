"""Three years of schedule history behave like a real office."""

import csv
import json
from collections import Counter, defaultdict
from datetime import date
from statistics import mean

import pytest

from sadhana.config import settings
from sadhana.simulator import charts, history
from sadhana.simulator.population import SIM_TODAY, generate

CONFIGS = settings.configs_dir
OFFICE = json.loads((CONFIGS / "offices" / "solo_manhattan.json").read_text())


def _simulate(seed: int = 42):
    with (CONFIGS / "payers" / "payers_NY.csv").open(newline="") as f:
        payers = {r["payer"]: r for r in csv.DictReader(f)}
    patients, truths = generate(OFFICE, CONFIGS, payers, seed=seed)
    fp, ft = history.former_patients(OFFICE, CONFIGS, payers, patients, seed=seed)
    everyone, all_truths = patients + fp, truths + ft
    _, _, perio, _ = charts.generate(everyone, all_truths, seed=seed)
    former_ids = {p.patient_id for p in fp}
    appts, appt_truth, hist, flows = history.run(OFFICE, everyone, all_truths, perio, CONFIGS, former_ids, seed=seed)
    return everyone, all_truths, perio, appts, appt_truth, hist, flows


@pytest.fixture(scope="module")
def sim():
    return _simulate()


def _past(appts, appt_truth, skip_left: bool = True):
    cause = {x.appt_id: x.cause for x in appt_truth}
    return [
        a
        for a in appts
        if date.fromisoformat(a.date) < SIM_TODAY and not (skip_left and cause[a.appt_id] == "left_practice")
    ]


def test_history_is_reproducible(sim) -> None:
    again = _simulate()
    assert [(a.date, a.start, a.status) for a in sim[3]] == [(a.date, a.start, a.status) for a in again[3]]


def test_no_double_booking(sim) -> None:
    appts = sim[3]
    by_column = defaultdict(list)
    for a in appts:
        if a.status != "cancelled":
            by_column[(a.date, a.provider_id)].append((a.start, a.end))
    for slots in by_column.values():
        slots.sort()
        for (_, end), (start, _) in zip(slots, slots[1:], strict=False):
            assert end <= start


def test_visits_respect_hours_lunch_holidays_and_staff_days(sim) -> None:
    appts = sim[3]
    staff_days = {s["id"]: set(s["days"]) for s in OFFICE["staff"]}
    closed = history.holidays(range(2023, 2028))
    lunch = OFFICE["lunch"]
    for a in appts:
        d = date.fromisoformat(a.date)
        day = history.DAYS[d.weekday()]
        opens, closes = OFFICE["hours"][day]
        assert day in staff_days[a.provider_id]
        assert d not in closed
        assert opens <= a.start and a.end <= closes
        assert a.end <= lunch[0] or a.start >= lunch[1]


def test_no_show_and_late_cancel_rates_match_offices_with_reminders(sim) -> None:
    past = _past(sim[3], sim[4])
    status = Counter(a.status for a in past)
    assert 0.04 <= status["no_show"] / len(past) <= 0.08
    assert 0.03 <= status["late_cancel"] / len(past) <= 0.06


def test_recall_gap_and_hygiene_utilization_are_realistic(sim) -> None:
    *_, perio, appts, _, hist, flows = sim
    assert 0.15 <= history.overdue_share(hist, flows, perio) <= 0.40
    assert 0.70 <= history.utilization(appts, OFFICE) <= 0.95


def test_holiday_weeks_and_bad_weather_bring_more_no_shows(sim) -> None:
    past = _past(sim[3], sim[4])

    def risky(d: date) -> bool:
        return history.holiday_week(d) or (d.weekday() == 4 and d.month in (6, 7, 8))

    def rate(keep) -> float:
        rows = [a for a in past if keep(date.fromisoformat(a.date))]
        return sum(a.status == "no_show" for a in rows) / len(rows)

    assert rate(risky) > rate(lambda d: not risky(d))
    weather_causes = Counter(x.cause for x in sim[4])
    assert weather_causes["weather"] > 0 and weather_causes["office_closed"] > 0


def test_kids_come_in_for_back_to_school(sim) -> None:
    past = [a for a in _past(sim[3], sim[4]) if a.status == "completed"]
    kids = Counter(int(a.date[5:7]) for a in past if a.kind == "recall_child")
    assert kids[8] > mean(kids[m] for m in range(1, 13))


def test_new_patients_arrive_every_month_with_a_december_dip(sim) -> None:
    new = [a for a in _past(sim[3], sim[4]) if a.kind == "new_patient" and a.status == "completed"]
    by_month = Counter(a.date[:7] for a in new)
    assert len(by_month) == 36
    month = Counter(int(a.date[5:7]) for a in new)
    assert month[12] < mean(month[m] for m in range(1, 13))


def test_former_patients_stop_coming_after_they_leave(sim) -> None:
    *_, appts, _, hist, flows = sim
    left = {f.patient_id: f.left_on for f in flows if f.left_on}
    assert left
    for a in appts:
        if a.patient_id in left and a.status == "completed":
            assert a.date < left[a.patient_id]
    status = {h.patient_id: h.status for h in hist}
    known = [f for f in flows if f.left_on and f.office_knows]
    assert known and all(status[f.patient_id] == "archived" for f in known)


def test_past_is_resolved_and_future_is_booked(sim) -> None:
    appts = sim[3]
    for a in appts:
        if date.fromisoformat(a.date) >= SIM_TODAY:
            # still on the books, unless the patient already cancelled it
            assert a.status == "scheduled" or (a.status == "cancelled" and date.fromisoformat(a.status_on) < SIM_TODAY)
        else:
            assert a.status != "scheduled"
    assert any(date.fromisoformat(a.date) >= SIM_TODAY for a in appts)


def test_toddlers_are_not_seen_before_age_two(sim) -> None:
    patients, *_, appts, _, _, _ = sim
    birth = {p.patient_id: date.fromisoformat(p.birth_date) for p in patients}
    for a in appts:
        d, b = date.fromisoformat(a.date), birth[a.patient_id]
        assert (d.year - b.year - ((d.month, d.day) < (b.month, b.day))) >= 2


def test_compliant_patients_keep_more_visits(sim) -> None:
    patients, truths, _, appts, _, _, flows = sim
    stay = {f.patient_id for f in flows if not f.left_on and f.joined_on < "2023-11-01"}
    done = Counter(a.patient_id for a in appts if a.status == "completed")
    ranked = sorted((t for t in truths if t.patient_id in stay), key=lambda t: t.recall_compliance)
    third = len(ranked) // 3
    low = mean(done[t.patient_id] for t in ranked[:third])
    high = mean(done[t.patient_id] for t in ranked[-third:])
    assert high > 1.5 * low
