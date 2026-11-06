"""The synthetic population is reproducible, matches each office, and behaves like real patients."""

import csv
import json
from collections import Counter
from statistics import mean

import pytest

from sadhana.config import settings
from sadhana.simulator.population import Patient, Truth, generate

CONFIGS = settings.configs_dir


def _office(office_id: str) -> dict:
    return json.loads((CONFIGS / "offices" / f"{office_id}.json").read_text())


def _payers(state: str = "NY") -> dict:
    with (CONFIGS / "payers" / f"payers_{state}.csv").open(newline="") as f:
        return {r["payer"]: r for r in csv.DictReader(f)}


@pytest.fixture(scope="module")
def solo() -> tuple[list[Patient], list[Truth]]:
    return generate(_office("solo_manhattan"), CONFIGS, _payers(), seed=42)


@pytest.fixture(scope="module")
def group() -> tuple[list[Patient], list[Truth]]:
    return generate(_office("group_queens"), CONFIGS, _payers(), seed=42)


def test_same_seed_same_population() -> None:
    a, _ = generate(_office("solo_manhattan"), CONFIGS, _payers(), seed=7)
    b, _ = generate(_office("solo_manhattan"), CONFIGS, _payers(), seed=7)
    assert [p.birth_date for p in a] == [p.birth_date for p in b]


def test_different_seed_different_population() -> None:
    a, _ = generate(_office("solo_manhattan"), CONFIGS, _payers(), seed=1)
    b, _ = generate(_office("solo_manhattan"), CONFIGS, _payers(), seed=2)
    assert [p.birth_date for p in a] != [p.birth_date for p in b]


@pytest.mark.parametrize(("office", "n"), [("solo", 2000), ("group", 6500)])
def test_patient_count(request: pytest.FixtureRequest, office: str, n: int) -> None:
    patients, truths = request.getfixturevalue(office)
    assert len(patients) == n
    assert [p.patient_id for p in patients] == [t.patient_id for t in truths]


@pytest.mark.parametrize(("office", "office_id"), [("solo", "solo_manhattan"), ("group", "group_queens")])
def test_payer_mix_matches_office(request: pytest.FixtureRequest, office: str, office_id: str) -> None:
    patients, _ = request.getfixturevalue(office)
    shares = Counter(p.payer for p in patients)
    for payer, target in _office(office_id)["payer_mix"].items():
        assert shares[payer] / len(patients) == pytest.approx(target, abs=0.035), payer


def test_solo_office_has_no_medicaid(solo: tuple[list[Patient], list[Truth]]) -> None:
    patients, _ = solo
    assert all(p.payer != "NY Medicaid" for p in patients)


def test_low_income_skews_to_medicaid(group: tuple[list[Patient], list[Truth]]) -> None:
    patients, truths = group
    tier = {t.patient_id: t.income_tier for t in truths}

    def medicaid_share(t: str) -> float:
        ps = [p for p in patients if tier[p.patient_id] == t]
        return sum(p.payer == "NY Medicaid" for p in ps) / len(ps)

    assert medicaid_share("low") > medicaid_share("middle") > medicaid_share("upper")


def test_households_share_zip_language_and_insurance(group: tuple[list[Patient], list[Truth]]) -> None:
    patients, _ = group
    by_house: dict[str, list[Patient]] = {}
    for p in patients:
        by_house.setdefault(p.household_id, []).append(p)
    for members in by_house.values():
        assert len({m.zip for m in members}) == 1
        assert len({m.payer for m in members}) == 1


def test_children_have_a_guardian_and_no_phone(group: tuple[list[Patient], list[Truth]]) -> None:
    patients, _ = group
    ids = {p.patient_id for p in patients}
    kids = [p for p in patients if p.age < 18]
    assert kids
    for k in kids:
        assert k.guardian_id in ids
        assert k.phone_type == "none" and not k.sms_consent


def test_texting_requires_a_cell_phone(group: tuple[list[Patient], list[Truth]]) -> None:
    patients, _ = group
    assert all(p.phone_type == "cell" for p in patients if p.sms_consent or p.marketing_consent)


def test_queens_is_younger_and_more_multilingual(
    solo: tuple[list[Patient], list[Truth]], group: tuple[list[Patient], list[Truth]]
) -> None:
    def share(ps: list[Patient], f) -> float:
        return sum(map(f, ps)) / len(ps)

    assert share(group[0], lambda p: p.age < 18) > share(solo[0], lambda p: p.age < 18)
    assert share(group[0], lambda p: p.language != "English") > share(solo[0], lambda p: p.language != "English")


def test_hidden_traits_are_probabilities(group: tuple[list[Patient], list[Truth]]) -> None:
    _, truths = group
    for t in truths:
        for v in (t.recall_compliance, t.no_show_rate, t.price_sensitivity, t.cosmetic_interest, t.text_responsiveness):
            assert 0 <= v <= 1


def test_no_show_and_late_cancel_rates_are_realistic(group: tuple[list[Patient], list[Truth]]) -> None:
    # benchmark: about 4-5 no-shows a week per 100 appointments at a typical office
    _, truths = group
    assert 0.04 <= mean(t.no_show_rate for t in truths) <= 0.08
    assert 0.03 <= mean(t.late_cancel_rate for t in truths) <= 0.07


def test_behavior_follows_real_patterns(group: tuple[list[Patient], list[Truth]]) -> None:
    patients, truths = group
    age = {p.patient_id: p.age for p in patients}

    def avg(attr: str, keep) -> float:
        return mean(getattr(t, attr) for t in truths if keep(t))

    # lower income is more price sensitive; younger adults reply to texts more; perio risk rises with age
    assert avg("price_sensitivity", lambda t: t.income_tier == "low") > avg(
        "price_sensitivity", lambda t: t.income_tier == "upper"
    )
    assert avg("text_responsiveness", lambda t: 18 <= age[t.patient_id] <= 34) > avg(
        "text_responsiveness", lambda t: age[t.patient_id] >= 65
    )
    assert avg("perio_risk", lambda t: age[t.patient_id] >= 55) > avg("perio_risk", lambda t: age[t.patient_id] < 35)


def test_health_form_is_noisy_like_real_data(group: tuple[list[Patient], list[Truth]]) -> None:
    patients, truths = group
    missing = sum(p.smoker is None for p in patients) / len(patients)
    wrong = sum(p.smoker is not None and p.smoker != t.smoker for p, t in zip(patients, truths, strict=True))
    assert 0.06 <= missing <= 0.14
    assert wrong > 0
