"""Synthetic patient population for one office.

Every patient has two halves:
- observed: what a practice management system would record (age, ZIP, language, insurance, consent, health form)
- truth: hidden traits that drive behavior later (recall compliance, no-show risk, price sensitivity, ...).
  Models never see truth; it exists so we can measure how close they get.

Households share ZIP, language, insurance and part of their behavior, the way real families do.
Same office + same seed = same population, every time.
"""

from __future__ import annotations

import csv
import json
import math
import random
from dataclasses import asdict, dataclass
from datetime import date
from pathlib import Path
from typing import Any

Row = dict[str, Any]

SIM_TODAY = date(2026, 11, 1)  # fixed "today" so runs are reproducible
AGE_BANDS = {
    "0-12": (0, 12),
    "13-17": (13, 17),
    "18-34": (18, 34),
    "35-54": (35, 54),
    "55-64": (55, 64),
    "65+": (65, 90),
}


@dataclass
class Patient:
    patient_id: str
    household_id: str
    role: str  # head, partner, child, adult_child, parent
    guardian_id: str
    birth_date: str
    age: int
    sex: str
    zip: str
    neighborhood: str
    language: str
    payer: str
    subscriber_id: str
    secondary_payer: str
    phone_type: str  # cell, landline, none
    email_on_file: bool
    sms_consent: bool
    marketing_consent: bool
    preferred_time: str
    smoker: bool | None  # None = health form not filled in
    diabetes: bool | None
    blood_thinner: bool | None
    pregnant: bool | None


@dataclass
class Truth:
    patient_id: str
    income_tier: str
    recall_compliance: float  # chance of returning on time for recall without outreach
    no_show_rate: float  # per-appointment chance of no-show before any reminder (office average about 6%)
    late_cancel_rate: float  # per-appointment chance of cancelling inside 24 hours (office average about 5%)
    price_sensitivity: float
    cosmetic_interest: float
    anxiety: float
    tech_comfort: float
    text_responsiveness: float  # chance of replying to a text
    reply_latency_min: float  # median minutes to reply
    financing_propensity: float
    trust_baseline: float
    caries_risk: float
    perio_risk: float
    grinding: bool
    sleep_apnea_risk: float
    smoker: bool
    diabetes: bool


def _clip(x: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, x))


def _sigmoid(x: float) -> float:
    return 1 / (1 + math.exp(-x))


def _pick(rng: random.Random, weights: dict[str, float]) -> str:
    keys = list(weights)
    return rng.choices(keys, weights=[weights[k] for k in keys])[0]


def _birth_date(rng: random.Random, age: int) -> date:
    day_of_year = rng.randint(0, 364)
    year = SIM_TODAY.year - age - (1 if day_of_year > SIM_TODAY.timetuple().tm_yday else 0)
    return date.fromordinal(date(year, 1, 1).toordinal() + day_of_year)


def _age_in_band(rng: random.Random, band: str) -> int:
    lo, hi = AGE_BANDS[band]
    return rng.randint(lo, hi)


def load_area(configs_dir: Path, state: str, area: str) -> tuple[Row, Row]:
    cfg: Row = json.loads((configs_dir / "simulator" / f"areas_{state}.json").read_text())
    return cfg, cfg["areas"][area]


def _household_members(rng: random.Random, area: Row) -> list[tuple[str, int]]:
    """(role, age) for each member of one household."""
    size = int(_pick(rng, area["household_size"]))
    head_age = _age_in_band(rng, _pick(rng, area["head_age_bands"]))
    members = [("head", head_age)]
    for _ in range(size - 1):
        if not any(r == "partner" for r, _ in members) and rng.random() < 0.75:
            members.append(("partner", max(18, head_age + rng.randint(-6, 6))))
        elif 24 <= head_age <= 58:
            members.append(("child", max(0, min(17, head_age - rng.randint(22, 40)))))
        elif head_age > 58:
            role = "adult_child" if rng.random() < 0.6 else "parent"
            age = head_age - rng.randint(25, 35) if role == "adult_child" else min(95, head_age + rng.randint(22, 30))
            members.append((role, max(18, age)))
        else:
            members.append(("adult_child" if head_age > 40 else "partner", max(18, head_age + rng.randint(-5, 5))))
    return members


def _payer_weights(
    cfg: Row, payer_types: dict[str, str], mix: dict[str, float], households: list[Row]
) -> dict[str, dict[str, float]]:
    """Rake payer weights so the patient-level mix matches the office's payer mix, while income tier
    and age still tilt each household (low income toward Medicaid, 65+ toward cash)."""
    tier_m, age_m = cfg["income_tier_payer_multipliers"], cfg["age65_payer_multipliers"]

    def tilt(h: Row, payer: str) -> float:
        ptype = payer_types[payer]
        m = tier_m[h["tier"]][ptype]
        return m * (age_m[ptype] if h["head_age"] >= 65 else 1.0)

    w = {p: share for p, share in mix.items()}
    total = sum(h["size"] for h in households)
    for _ in range(60):
        expected = dict.fromkeys(mix, 0.0)
        for h in households:
            row = {p: w[p] * tilt(h, p) for p in mix}
            z = sum(row.values())
            for p in mix:
                expected[p] += h["size"] * row[p] / z
        for p in mix:
            if expected[p] > 0:
                w[p] *= (mix[p] * total) / expected[p]
    h_tilts: dict[str, dict[str, float]] = {}
    for h in households:
        row = {p: w[p] * tilt(h, p) for p in mix}
        z = sum(row.values())
        h_tilts[h["household_id"]] = {p: v / z for p, v in row.items()}
    return h_tilts


def _truth(rng: random.Random, pid: str, age: int, sex: str, tier: str, payer_type: str, hh_shared: float) -> Truth:
    g = rng.gauss
    young = 1.0 if 18 <= age <= 34 else 0.0
    senior = 1.0 if age >= 65 else 0.0
    tier_n = {"low": -1.0, "middle": 0.0, "upper": 1.0}[tier]

    anxiety = _clip(rng.betavariate(2, 5))
    compliance = _sigmoid(
        0.2 + 0.9 * hh_shared + 0.35 * tier_n - 0.6 * young + 0.3 * senior - 0.8 * anxiety + g(0, 0.6)
    )
    # a small group misses visits chronically, which is where most no-shows come from in real offices
    chronic = rng.random() < 0.06
    no_show = _clip(
        0.0
        + 0.07 * (1 - compliance)
        + chronic * rng.uniform(0.12, 0.3)
        + 0.03 * young
        + 0.04 * anxiety
        + 0.02 * (payer_type == "medicaid")
        + g(0, 0.012),
        0.005,
        0.4,
    )
    late_cancel = _clip(
        0.01 + 0.06 * (1 - compliance) + 0.02 * young + chronic * rng.uniform(0.03, 0.1) + g(0, 0.012), 0.005, 0.35
    )
    price = _sigmoid(-0.9 * tier_n + 0.6 * (payer_type in ("self_pay", "medicaid")) + 0.3 * senior + g(0, 0.5))
    cosmetic = _sigmoid(-1.2 + 0.7 * tier_n + 0.8 * (18 <= age <= 54) - 0.6 * senior + 0.3 * (sex == "F") + g(0, 0.7))
    tech = _sigmoid(1.8 - 0.05 * max(0, age - 30) + 0.3 * tier_n + g(0, 0.5))
    responsive = _clip(0.25 + 0.45 * tech + 0.1 * compliance + g(0, 0.08), 0.02, 0.95)
    latency = math.exp(g(math.log(40 + 200 * (1 - tech)), 0.9))
    financing = _sigmoid(-0.8 + 0.8 * price + 0.4 * (25 <= age <= 54) + g(0, 0.5))
    trust = _clip(0.5 + 0.2 * compliance - 0.2 * anxiety + g(0, 0.12))
    smoker = rng.random() < (0.16 if tier == "low" else 0.09 if tier == "middle" else 0.06) * (age >= 18)
    diabetes = rng.random() < (0.02 + 0.004 * max(0, age - 30)) * (1.4 if tier == "low" else 1.0)
    caries = _sigmoid(-0.4 - 0.5 * tier_n + 0.5 * (age < 18) + 0.4 * smoker - 0.8 * compliance + g(0, 0.5))
    perio = _sigmoid(-2.2 + 0.035 * age + 0.9 * smoker + 0.8 * diabetes - 0.6 * compliance + g(0, 0.5))
    grinding = rng.random() < 0.12 + 0.12 * anxiety
    apnea = _sigmoid(-3.2 + 0.03 * age + 0.6 * (sex == "M") + g(0, 0.5)) if age >= 18 else 0.01
    return Truth(
        patient_id=pid,
        income_tier=tier,
        recall_compliance=round(compliance, 3),
        no_show_rate=round(no_show, 3),
        late_cancel_rate=round(late_cancel, 3),
        price_sensitivity=round(price, 3),
        cosmetic_interest=round(cosmetic, 3),
        anxiety=round(anxiety, 3),
        tech_comfort=round(tech, 3),
        text_responsiveness=round(responsive, 3),
        reply_latency_min=round(latency, 1),
        financing_propensity=round(financing, 3),
        trust_baseline=round(trust, 3),
        caries_risk=round(caries, 3),
        perio_risk=round(perio, 3),
        grinding=grinding,
        sleep_apnea_risk=round(apnea, 3),
        smoker=smoker,
        diabetes=diabetes,
    )


def _health_form(rng: random.Random, value: bool) -> bool | None:
    """What the health history form says: missing 10% of the time, wrong 3% of the time."""
    r = rng.random()
    if r < 0.10:
        return None
    if r < 0.13:
        return not value
    return value


def generate(
    office: Row, configs_dir: Path, payers: dict[str, Row], seed: int = 42
) -> tuple[list[Patient], list[Truth]]:
    rng = random.Random(f"{office['office_id']}-{seed}")
    cfg, area = load_area(configs_dir, office["state"], office["simulation"]["area"])
    target = int(office["simulation"]["active_patients"])
    mix: dict[str, float] = office["payer_mix"]
    payer_types = {p: payers[p]["payer_type"] for p in mix}

    # 1. households: size, members, ZIP, income tier, language
    households: list[Row] = []
    count = 0
    while count < target:
        members = _household_members(rng, area)
        members = members[: target - count]
        z = rng.choices(area["zips"], weights=[x["weight"] for x in area["zips"]])[0]
        households.append(
            {
                "household_id": f"H{len(households) + 1:05d}",
                "members": members,
                "size": len(members),
                "head_age": members[0][1],
                "zip": z["zip"],
                "neighborhood": z["neighborhood"],
                "tier": z["income_tier"],
                "language": _pick(rng, area["languages"]),
                "shared": rng.gauss(0, 1),
            }
        )
        count += len(members)

    # 2. insurance per household, raked to the office's payer mix
    tilts = _payer_weights(cfg, payer_types, mix, households)

    patients: list[Patient] = []
    truths: list[Truth] = []
    pid_n = 0
    for h in households:
        payer = _pick(rng, tilts[h["household_id"]])
        ptype = payer_types[payer]
        head_id = ""
        for role, age in h["members"]:
            pid_n += 1
            pid = f"P{pid_n:06d}"
            if role == "head":
                head_id = pid
            sex = rng.choice(["F", "M"])
            # adults sometimes speak English with the office even if the household language differs
            lang = h["language"] if (role == "child" or rng.random() < 0.85) else "English"
            secondary = ""
            if role == "partner" and ptype in ("ppo", "premier") and rng.random() < cfg["dual_coverage_rate"] * 3:
                secondary = rng.choice([p for p in mix if payer_types[p] in ("ppo", "premier") and p != payer] or [""])
            t = _truth(rng, pid, age, sex, h["tier"], ptype, h["shared"])
            if age < 18:
                phone = "none"
            elif age >= 65:
                phone = rng.choices(["cell", "landline", "none"], [0.80, 0.16, 0.04])[0]
            else:
                phone = rng.choices(["cell", "landline", "none"], [0.95, 0.02, 0.03])[0]
            patients.append(
                Patient(
                    patient_id=pid,
                    household_id=h["household_id"],
                    role=role,
                    guardian_id=head_id if age < 18 and role != "head" else "",
                    birth_date=_birth_date(rng, age).isoformat(),
                    age=age,
                    sex=sex,
                    zip=h["zip"],
                    neighborhood=h["neighborhood"],
                    language=lang,
                    payer=payer,
                    subscriber_id=head_id if role in ("child", "partner") else pid,
                    secondary_payer=secondary,
                    phone_type=phone,
                    email_on_file=rng.random() < (0.55 + 0.35 * t.tech_comfort) and age >= 18,
                    sms_consent=phone == "cell" and rng.random() < 0.88,
                    marketing_consent=phone == "cell" and rng.random() < 0.20 + 0.25 * t.cosmetic_interest,
                    preferred_time=rng.choices(
                        ["early_morning", "midday", "evening", "saturday", "any"], [0.25, 0.15, 0.25, 0.15, 0.20]
                    )[0]
                    if age >= 18
                    else "after_school",
                    smoker=_health_form(rng, t.smoker),
                    diabetes=_health_form(rng, t.diabetes),
                    blood_thinner=_health_form(rng, age >= 65 and rng.random() < 0.15),
                    pregnant=_health_form(rng, sex == "F" and 20 <= age <= 42 and rng.random() < 0.05),
                )
            )
            truths.append(t)
    return patients, truths


def write(patients: list[Patient], truths: list[Truth], out_dir: Path) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    for name, rows in (("patients.csv", patients), ("truth.csv", truths)):
        with (out_dir / name).open("w", newline="") as f:
            dicts = [asdict(r) for r in rows]
            writer = csv.DictWriter(f, fieldnames=list(dicts[0]))
            writer.writeheader()
            writer.writerows(dicts)
    return out_dir
