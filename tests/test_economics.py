"""Economics outputs are internally consistent and route work the way real offices do."""

import pytest

from sadhana.economics.engine import OfficeInputs, build


@pytest.mark.parametrize("office", ["solo", "group"])
def test_amounts_are_consistent(request: pytest.FixtureRequest, office: str) -> None:
    result = build(request.getfixturevalue(office))
    assert result.detail
    for r in result.detail:
        assert r["allowed"] <= r["office_fee"] + 0.01
        assert r["collected"] <= r["allowed"]
        assert 0 <= r["writeoff_pct"] <= 1


@pytest.mark.parametrize("office", ["solo", "group"])
def test_every_payer_mix_sums_to_one(request: pytest.FixtureRequest, office: str) -> None:
    inputs: OfficeInputs = request.getfixturevalue(office)
    assert sum(inputs.office["payer_mix"].values()) == pytest.approx(1.0)


def test_group_routes_work_to_the_right_people(group: OfficeInputs) -> None:
    best = {r["code"]: r["best_provider"] for r in build(group).summary}
    assert best["D3330"] == "S1"  # molar root canal goes to the endodontist
    assert best["D2392"] == "D3"  # routine filling goes to the junior associate
    assert best["D1110"].startswith("H")  # cleanings go to a hygienist
    assert best["D0274"].startswith("A")  # x-rays go to an assistant


def test_medicaid_patients_carry_no_cancel_fee(group: OfficeInputs) -> None:
    payers = {p["payer"]: p for p in build(group).payers}
    assert payers["NY Medicaid"]["expected_late_cancel_fee"] == 0
    assert payers["Delta Dental PPO"]["expected_late_cancel_fee"] > 0
