"""Dental charts look like real mouths and real exam records."""

import csv
import json
from collections import Counter

import pytest

from sadhana.config import settings
from sadhana.simulator import charts
from sadhana.simulator.population import generate

CONFIGS = settings.configs_dir


@pytest.fixture(scope="module")
def sim():
    office = json.loads((CONFIGS / "offices" / "group_queens.json").read_text())
    with (CONFIGS / "payers" / "payers_NY.csv").open(newline="") as f:
        payers = {r["payer"]: r for r in csv.DictReader(f)}
    patients, truths = generate(office, CONFIGS, payers, seed=42)
    teeth, conds, perio, plan = charts.generate(patients, truths, seed=42)
    return patients, truths, teeth, conds, perio, plan


def test_charts_are_reproducible(sim) -> None:
    patients, truths, *_ = sim
    a = charts.generate(patients[:200], truths[:200], seed=3)
    b = charts.generate(patients[:200], truths[:200], seed=3)
    assert [x.restoration for x in a[0]] == [x.restoration for x in b[0]]


def test_tooth_sets_match_age(sim) -> None:
    patients, _, teeth, *_ = sim
    by_patient: dict[str, list[str]] = {}
    for t in teeth:
        by_patient.setdefault(t.patient_id, []).append(t.tooth)
    for p in patients:
        names = by_patient[p.patient_id]
        if p.age < 6:
            assert all(not n.isdigit() for n in names) and len(names) == 20
        elif p.age >= 12:
            assert len(names) == 32 and all(n.isdigit() for n in names)


def test_missing_teeth_rise_with_age(sim) -> None:
    patients, _, teeth, *_ = sim
    ages = {p.patient_id: p.age for p in patients}

    def avg_missing(lo: int, hi: int) -> float:
        ids = {p.patient_id for p in patients if lo <= p.age <= hi}
        n = sum(
            1
            for t in teeth
            if t.patient_id in ids and not t.present and t.tooth.isdigit() and int(t.tooth) not in charts.WISDOM
        )
        return n / len(ids)

    assert avg_missing(20, 34) < avg_missing(35, 49) < avg_missing(50, 64) < avg_missing(65, 120)
    assert all(t.present for t in teeth if ages[t.patient_id] < 12)


def test_adult_decay_rate_is_realistic(sim) -> None:
    # CDC: roughly one in four US adults has untreated decay
    patients, _, _, conds, *_ = sim
    adults = [p for p in patients if p.age >= 20]
    decay = {c.patient_id for c in conds if c.condition == "caries"}
    assert 0.12 <= sum(p.patient_id in decay for p in adults) / len(adults) <= 0.32


def test_perio_rises_with_age(sim) -> None:
    patients, _, _, _, perio, _ = sim
    ages = {p.patient_id: p.age for p in patients}
    sick = {"stage_1", "stage_2", "stage_3", "stage_4"}

    def share(lo: int, hi: int) -> float:
        rows = [x for x in perio if lo <= ages[x.patient_id] <= hi]
        return sum(x.true_stage in sick for x in rows) / len(rows)

    assert share(18, 34) < share(55, 120)
    # CDC: about 4 in 10 adults 30+ have periodontitis
    assert 0.3 <= share(30, 120) <= 0.6


def test_exams_miss_some_problems(sim) -> None:
    *_, conds, _, _ = sim
    found = sum(c.diagnosed for c in conds) / len(conds)
    assert 0.8 <= found < 1.0


def test_plan_only_uses_catalog_codes(sim) -> None:
    *_, plan = sim
    with (CONFIGS / "catalog" / "service_catalog.csv").open(newline="") as f:
        codes = {r["code"] for r in csv.DictReader(f)}
    assert {x.code for x in plan} <= codes
    assert all(x.status == "proposed" for x in plan)


def test_every_tooth_level_plan_item_traces_to_a_diagnosis(sim) -> None:
    *_, conds, _, plan = sim
    diagnosed = {(c.patient_id, c.tooth) for c in conds if c.diagnosed}
    sealant_or_implant = {"D1351", "D6010", "D6057", "D6065"}
    for x in plan:
        if x.tooth and not x.tooth.startswith("Q") and x.code not in sealant_or_implant:
            assert (x.patient_id, x.tooth) in diagnosed, x


def test_root_canal_comes_with_buildup_and_crown(sim) -> None:
    *_, plan = sim
    by_tooth: dict[tuple[str, str], set[str]] = {}
    for x in plan:
        by_tooth.setdefault((x.patient_id, x.tooth), set()).add(x.code)
    for codes in by_tooth.values():
        if codes & {"D3310", "D3320", "D3330"}:
            assert {"D2950", "D2740"} <= codes


def test_cosmetic_items_need_interest(sim) -> None:
    patients, truths, *_, plan = sim
    consent = {p.patient_id: p.marketing_consent for p in patients}
    interest = {t.patient_id: t.cosmetic_interest for t in truths}
    for x in plan:
        if x.code == "D9972":
            assert consent[x.patient_id] and interest[x.patient_id] > 0.6


def test_plan_mix_looks_like_a_real_office(sim) -> None:
    *_, plan = sim
    codes = Counter(x.code for x in plan)
    assert codes["D2391"] + codes["D2392"] + codes["D2393"] > codes["D6010"]  # fillings outnumber implants
    assert codes["D2740"] > 0 and codes["D4341"] > 0 and codes["D9944"] > 0
