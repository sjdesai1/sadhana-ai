"""Three years of schedule history behave like a real office."""

from collections import Counter, defaultdict
from datetime import date
from statistics import mean

from sadhana.config import settings
from sadhana.simulator import history
from sadhana.simulator.build import simulate
from sadhana.simulator.population import SIM_TODAY

HYGIENE = {"new_patient", "recall_adult", "recall_child", "perio_maintenance"}


def _past(sim, skip_left: bool = True, kinds: set[str] | None = HYGIENE):
    cause = {x.appt_id: x.cause for x in sim.appointment_truth}
    return [
        a
        for a in sim.appointments
        if date.fromisoformat(a.date) < SIM_TODAY
        and not (skip_left and cause[a.appt_id] == "left_practice")
        and (kinds is None or a.kind in kinds)
    ]


def test_history_is_reproducible(solo_sim) -> None:
    again = simulate("solo_manhattan", settings.configs_dir, seed=42)
    assert [(a.date, a.start, a.status) for a in solo_sim.appointments] == [
        (a.date, a.start, a.status) for a in again.appointments
    ]


def test_no_double_booking(solo_sim, group_sim) -> None:
    for sim in (solo_sim, group_sim):
        by_column = defaultdict(list)
        by_patient = defaultdict(list)
        for a in sim.appointments:
            if a.status != "cancelled":
                by_column[(a.date, a.provider_id)].append((a.start, a.end))
                by_patient[(a.date, a.patient_id)].append((a.start, a.end))
        for slots in [*by_column.values(), *by_patient.values()]:
            slots.sort()
            for (_, end), (start, _) in zip(slots, slots[1:], strict=False):
                assert end <= start


def test_visits_respect_hours_lunch_holidays_and_staff_days(solo_sim, group_sim) -> None:
    closed = history.holidays(range(2023, 2028))
    for sim in (solo_sim, group_sim):
        office = sim.office
        staff = {s["id"]: s for s in office["staff"]}
        lunch = office["lunch"]
        for a in sim.appointments:
            d = date.fromisoformat(a.date)
            day = history.DAYS[d.weekday()]
            opens, closes = office["hours"][day]
            assert day in staff[a.provider_id]["days"]
            assert d not in closed
            assert opens <= a.start and a.end <= closes
            assert a.end <= lunch[0] or a.start >= lunch[1]


def test_visiting_specialists_only_come_on_their_weeks(group_sim) -> None:
    for a in group_sim.appointments:
        if a.column == "specialist":
            week = (date.fromisoformat(a.date).day - 1) // 7
            assert week in ((0, 2) if a.provider_id == "S1" else (1,))


def test_no_show_and_late_cancel_rates_match_offices_with_reminders(solo_sim) -> None:
    past = _past(solo_sim, kinds=None)
    status = Counter(a.status for a in past)
    assert 0.04 <= status["no_show"] / len(past) <= 0.08
    assert 0.03 <= status["late_cancel"] / len(past) <= 0.06


def test_recall_gap_and_chair_time_are_realistic(solo_sim, group_sim) -> None:
    for sim in (solo_sim, group_sim):
        assert 0.15 <= history.overdue_share(sim.history, sim.flows, sim.perio) <= 0.40
        assert 0.75 <= history.utilization(sim.appointments, sim.office, "hygiene") <= 0.95
        # doctors have open time left: part of what the product fills
        assert 0.50 <= history.utilization(sim.appointments, sim.office, "doctor") <= 0.90


def test_holiday_weeks_and_bad_weather_bring_more_no_shows(solo_sim) -> None:
    past = _past(solo_sim)

    def risky(d: date) -> bool:
        return history.holiday_week(d) or (d.weekday() == 4 and d.month in (6, 7, 8))

    def rate(keep) -> float:
        rows = [a for a in past if keep(date.fromisoformat(a.date))]
        return sum(a.status == "no_show" for a in rows) / len(rows)

    assert rate(risky) > rate(lambda d: not risky(d))
    causes = Counter(x.cause for x in solo_sim.appointment_truth)
    assert causes["weather"] > 0 and causes["office_closed"] > 0


def test_kids_come_in_for_back_to_school(solo_sim) -> None:
    done = [a for a in _past(solo_sim) if a.status == "completed"]
    kids = Counter(int(a.date[5:7]) for a in done if a.kind == "recall_child")
    assert kids[8] > mean(kids[m] for m in range(1, 13))


def test_new_patients_arrive_every_month_with_a_december_dip(solo_sim) -> None:
    new = [a for a in _past(solo_sim) if a.kind == "new_patient" and a.status == "completed"]
    assert len(Counter(a.date[:7] for a in new)) == 36
    month = Counter(int(a.date[5:7]) for a in new)
    assert month[12] < mean(month[m] for m in range(1, 13))


def test_former_patients_stop_coming_after_they_leave(solo_sim) -> None:
    left = {f.patient_id: f.left_on for f in solo_sim.flows if f.left_on}
    assert left
    for a in solo_sim.appointments:
        if a.patient_id in left and a.status == "completed":
            assert a.date < left[a.patient_id]
    status = {h.patient_id: h.status for h in solo_sim.history}
    known = [f for f in solo_sim.flows if f.left_on and f.office_knows]
    assert known and all(status[f.patient_id] == "archived" for f in known)


def test_past_is_resolved_and_future_is_booked(solo_sim) -> None:
    for a in solo_sim.appointments:
        if date.fromisoformat(a.date) >= SIM_TODAY:
            # still on the books, unless the patient already cancelled it
            assert a.status == "scheduled" or (a.status == "cancelled" and date.fromisoformat(a.status_on) < SIM_TODAY)
        else:
            assert a.status != "scheduled"
    assert any(date.fromisoformat(a.date) >= SIM_TODAY for a in solo_sim.appointments)


def test_toddlers_are_not_seen_before_age_two(solo_sim) -> None:
    birth = {p.patient_id: date.fromisoformat(p.birth_date) for p in solo_sim.patients}
    for a in solo_sim.appointments:
        if a.kind != "emergency":
            d, b = date.fromisoformat(a.date), birth[a.patient_id]
            assert (d.year - b.year - ((d.month, d.day) < (b.month, b.day))) >= 2


def test_compliant_patients_keep_more_cleanings(solo_sim) -> None:
    stay = {f.patient_id for f in solo_sim.flows if not f.left_on and f.joined_on < "2023-11-01"}
    done = Counter(a.patient_id for a in solo_sim.appointments if a.status == "completed" and a.kind in HYGIENE)
    ranked = sorted((t for t in solo_sim.truths if t.patient_id in stay), key=lambda t: t.recall_compliance)
    third = len(ranked) // 3
    low = mean(done[t.patient_id] for t in ranked[:third])
    high = mean(done[t.patient_id] for t in ranked[-third:])
    assert high > 1.5 * low
