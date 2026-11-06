"""Treatment history: diagnosis, decisions, sequencing and routing behave like a real office."""

from collections import Counter, defaultdict
from datetime import date
from statistics import mean

from sadhana.config import settings
from sadhana.economics.engine import can_perform
from sadhana.simulator.treatment import ROOT_CANAL, load_catalog, plan_value

CONFIGS = settings.configs_dir
CATALOG = load_catalog(CONFIGS)


def test_every_completed_item_has_a_completed_visit(solo_sim) -> None:
    appts = {a.appt_id: a for a in solo_sim.appointments}
    for x in solo_sim.plan:
        if x.status == "completed":
            a = appts[x.appt_id]
            assert a.status == "completed" and x.code in a.codes.split()
            assert x.diagnosed_on <= a.date


def test_procedures_ledger_matches_completed_visits(solo_sim) -> None:
    appts = {a.appt_id: a for a in solo_sim.appointments}
    for p in solo_sim.procedures:
        assert appts[p.appt_id].status == "completed" and p.date == appts[p.appt_id].date
    posted = {x.plan_id for x in solo_sim.plan if x.status == "completed"}
    assert posted == {p.plan_id for p in solo_sim.procedures if p.plan_id}


def test_crown_after_root_canal_and_implant_crown_after_healing(group_sim) -> None:
    done: dict[tuple[str, str], dict[str, str]] = defaultdict(dict)
    for x in group_sim.plan:
        if x.status == "completed":
            done[(x.patient_id, x.tooth)][x.code] = x.status_on
    checked = 0
    for codes in done.values():
        rct = [d for c, d in codes.items() if c in ROOT_CANAL]
        if rct and "D2740" in codes:
            assert codes["D2740"] > rct[0]
            checked += 1
        if "D6010" in codes and "D6065" in codes:
            healed = date.fromisoformat(codes["D6065"]) - date.fromisoformat(codes["D6010"])
            assert healed.days >= 90
    assert checked > 0


def test_providers_only_do_what_they_are_allowed_to(group_sim) -> None:
    staff = {s["id"]: s for s in group_sim.office["staff"]}
    for p in group_sim.procedures:
        if p.plan_id:
            assert can_perform(staff[p.provider_id], CATALOG[p.code], group_sim.office)[0], p


def test_group_sends_root_canals_to_the_visiting_endodontist(group_sim) -> None:
    by = Counter(p.provider_id for p in group_sim.procedures if p.code in ROOT_CANAL)
    assert by and set(by) == {"S1"}


def test_solo_office_refers_out_implants(solo_sim) -> None:
    # no cone beam CT in the solo office, so implant placement is referred
    statuses = Counter(x.status for x in solo_sim.plan if x.code == "D6010")
    assert statuses["referred"] > 0 and statuses["completed"] == 0


def test_urgent_work_gets_done_more_than_elective(solo_sim, group_sim) -> None:
    for sim in (solo_sim, group_sim):
        decided = [x for x in sim.plan if x.times_presented > 0 and x.status != "referred"]

        share = {}
        for priority in ("urgent", "needed", "elective"):
            rows = [x for x in decided if x.priority == priority]
            share[priority] = sum(x.status == "completed" for x in rows) / len(rows)
        assert share["urgent"] > share["needed"] > share["elective"]


def test_price_sensitive_patients_accept_less(group_sim) -> None:
    sensitivity = {t.patient_id: t.price_sensitivity for t in group_sim.truths}
    rows = [x for x in group_sim.plan if x.priority == "needed" and x.times_presented > 0]
    ranked = sorted(rows, key=lambda x: sensitivity[x.patient_id])
    third = len(ranked) // 3
    yes = {"accepted", "scheduled", "completed"}
    low = mean(x.status in yes for x in ranked[:third])
    high = mean(x.status in yes for x in ranked[-third:])
    assert low > high


def test_later_exams_find_new_problems(solo_sim) -> None:
    later = solo_sim.conditions[solo_sim.chart_conditions :]
    assert later and all(c.diagnosed and c.found_on for c in later)
    assert Counter(c.condition for c in later)["caries"] > 0


def test_deep_cleaning_moves_patients_to_perio_maintenance(group_sim) -> None:
    srp_done = {p.patient_id: p.date for p in group_sim.procedures if p.code in ("D4341", "D4342")}
    after = {a.patient_id for a in group_sim.appointments if a.kind == "perio_maintenance"}
    moved = [pid for pid in srp_done if pid in after]
    assert len(moved) > 0.3 * len(srp_done)


def test_emergencies_come_from_real_problems(solo_sim) -> None:
    emerg = [a for a in solo_sim.appointments if a.kind == "emergency" and a.status == "completed"]
    assert len(emerg) > 36  # at least one a month
    assert all(a.column == "doctor" for a in emerg)


def test_unscheduled_treatment_is_a_large_pile(solo_sim, group_sim) -> None:
    for sim in (solo_sim, group_sim):
        value = plan_value(sim.plan, sim.office, CATALOG, sim.current_ids)
        total = sum(value.values())
        open_value = value.get("accepted", 0) + value.get("proposed", 0)
        assert 0.2 <= open_value / total <= 0.6
        assert value["completed"] > 0 and value["declined"] > 0


def test_never_examined_patients_have_no_plan(solo_sim) -> None:
    examined = {a.patient_id for a in solo_sim.appointments if a.status == "completed"}
    established = {f.patient_id for f in solo_sim.flows if f.joined_on < "2023-11-01"}
    for x in solo_sim.plan:
        assert x.patient_id in examined or x.patient_id in established
