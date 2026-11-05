"""NY scope of practice and office rules are enforced."""

from typing import Any

import pytest

from sadhana.economics.engine import OfficeInputs, allowed_amount, can_perform


def proc(inputs: OfficeInputs, code: str) -> dict[str, Any]:
    return next(p for p in inputs.catalog if p["code"] == code)


def staff(inputs: OfficeInputs, sid: str) -> dict[str, Any]:
    return next(s for s in inputs.office["staff"] if s["id"] == sid)


@pytest.mark.parametrize(
    ("office", "sid", "code", "expected"),
    [
        ("solo", "A1", "D1351", False),  # RDA cannot place sealants
        ("solo", "H1", "D1351", True),  # hygienist can
        ("solo", "A1", "D1206", True),  # RDA can apply fluoride
        ("solo", "A2", "D1206", False),  # unlicensed assistant cannot
        ("solo", "A2", "D0274", True),  # unlicensed assistant can take x-rays
        ("solo", "H2", "D4341", False),  # SRP needs the anesthesia cert
        ("solo", "H1", "D4341", True),
        ("group", "D3", "D6010", False),  # junior associate does not place implants
        ("group", "D3", "D3330", False),  # or molar root canals
        ("group", "S1", "D3330", True),  # endodontist does
        ("group", "S1", "D2740", False),  # endodontist does not do crowns here
        ("solo", "D1", "D0367", False),  # solo office has no CBCT
        ("group", "D1", "D0367", True),
    ],
)
def test_eligibility(request: pytest.FixtureRequest, office: str, sid: str, code: str, expected: bool) -> None:
    inputs: OfficeInputs = request.getfixturevalue(office)
    ok, _ = can_perform(staff(inputs, sid), proc(inputs, code), inputs.office)
    assert ok is expected


def test_medicaid_rules(group: OfficeInputs) -> None:
    medicaid = group.payers["NY Medicaid"]
    assert allowed_amount(proc(group, "D9972"), medicaid, group.office, group.medicaid)[0] is None
    assert allowed_amount(proc(group, "D1110"), medicaid, group.office, group.medicaid)[0] == 45.45


def test_cigna_pays_less_than_united(solo: OfficeInputs) -> None:
    crown = proc(solo, "D2740")
    cigna = allowed_amount(crown, solo.payers["Cigna DPPO"], solo.office, solo.medicaid)[0]
    united = allowed_amount(crown, solo.payers["UnitedHealthcare Dental"], solo.office, solo.medicaid)[0]
    assert cigna is not None and united is not None
    assert cigna < united
