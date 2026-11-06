"""Dental charts for the synthetic population.

For every patient:
- teeth: which teeth are present (baby, mixed or permanent by age) and the work already done on each
- conditions (truth): what is actually wrong in the mouth right now
- diagnosed: the subset the dentist found and charted; some problems are missed, like in real exams
- perio: true gum disease stage and the stage on the chart
- treatment plan: diagnosed problems turned into catalog procedures, status "proposed"; this is the
  unscheduled treatment the product follows up on

Universal numbering: permanent teeth 1-32 (1, 16, 17, 32 are wisdom teeth), baby teeth A-T.
"""

from __future__ import annotations

import csv
import random
from dataclasses import asdict, dataclass
from pathlib import Path

from sadhana.simulator.population import Patient, Truth

MOLARS = {1, 2, 3, 14, 15, 16, 17, 18, 19, 30, 31, 32}
PREMOLARS = {4, 5, 12, 13, 20, 21, 28, 29}
WISDOM = {1, 16, 17, 32}
FIRST_MOLARS = {3, 14, 19, 30}
SECOND_MOLARS = {2, 15, 18, 31}
INCISORS = {7, 8, 9, 10, 23, 24, 25, 26}
UPPER = set(range(1, 17))
PRIMARY = [chr(c) for c in range(ord("A"), ord("T") + 1)]
QUADS = {1: range(1, 9), 2: range(9, 17), 3: range(17, 25), 4: range(25, 33)}
DETECTION = {"caries": 0.85, "cracked": 0.6, "failing_restoration": 0.8, "impacted": 0.95, "periapical": 0.9}


@dataclass
class Tooth:
    patient_id: str
    tooth: str
    kind: str  # molar, premolar, anterior, primary
    present: bool
    missing_reason: str  # extracted, never_erupted, congenital, ""
    restoration: str  # none, filling, crown, implant_crown
    filling_surfaces: int
    restoration_age_years: int
    root_canal: bool


@dataclass
class Condition:
    patient_id: str
    tooth: str
    condition: str  # caries, cracked, failing_restoration, impacted, periapical, missing_unreplaced
    severity: str  # early, moderate, deep, ""
    diagnosed: bool
    found_on: str = ""  # filled in by the history: exam or emergency where it was found
    treated_on: str = ""


@dataclass
class Perio:
    patient_id: str
    true_stage: str  # healthy, gingivitis, stage_1 .. stage_4
    charted_stage: str
    bleeding: bool
    grinding_wear: bool
    maintenance: bool = False  # already had scaling and root planing, now on perio maintenance


@dataclass
class PlanItem:
    patient_id: str
    tooth: str
    code: str
    reason: str
    priority: str  # urgent, needed, elective
    status: str  # proposed; the history moves it to accepted, scheduled, completed, declined or referred
    plan_id: str = ""
    diagnosed_on: str = ""
    status_on: str = ""
    appt_id: str = ""
    times_presented: int = 0


def _kind(t: int) -> str:
    return "molar" if t in MOLARS else "premolar" if t in PREMOLARS else "anterior"


def _teeth_for_age(age: int) -> list[str]:
    if age < 6:
        return PRIMARY
    if age < 12:
        keep_primary = PRIMARY[3:7] + PRIMARY[13:17] if age < 9 else PRIMARY[4:6] + PRIMARY[14:16]
        permanent = sorted(FIRST_MOLARS | INCISORS) + (sorted(SECOND_MOLARS) if age >= 11 else [])
        return [str(t) for t in permanent] + keep_primary
    return [str(t) for t in range(1, 33)]


def _missing_count(rng: random.Random, age: int, tier: str, compliance: float) -> int:
    """Average adult missing teeth (excluding wisdom) rises with age; lower income and poor care add more."""
    if age < 20:
        return 0
    base = 0.4 if age < 35 else 1.8 if age < 50 else 4.5 if age < 65 else 8.5
    base *= {"low": 1.5, "middle": 1.0, "upper": 0.7}[tier] * (1.4 - 0.6 * compliance)
    return min(24, max(0, round(rng.gauss(base, base * 0.6 + 0.3))))


def _perio_stage(rng: random.Random, age: int, truth: Truth) -> str:
    if age < 18:
        return "gingivitis" if rng.random() < 0.25 else "healthy"
    r = truth.perio_risk + rng.gauss(0, 0.12)
    if r < 0.2:
        return "healthy"
    if r < 0.4:
        return "gingivitis"
    if r < 0.55:
        return "stage_1"
    if r < 0.72:
        return "stage_2"
    if r < 0.88:
        return "stage_3"
    return "stage_4"


def _filling_code(tooth: int, surfaces: int) -> str:
    if _kind(tooth) == "anterior":
        return "D2330"
    return {1: "D2391", 2: "D2392"}.get(surfaces, "D2393")


def _root_canal_code(tooth: int) -> str:
    return {"anterior": "D3310", "premolar": "D3320", "molar": "D3330"}[_kind(tooth)]


def chart_patient(
    rng: random.Random, p: Patient, t: Truth
) -> tuple[list[Tooth], list[Condition], Perio, list[PlanItem]]:
    teeth: list[Tooth] = []
    conds: list[Condition] = []
    plan: list[PlanItem] = []
    age = p.age
    names = _teeth_for_age(age)

    def add_cond(tooth: str, condition: str, severity: str = "") -> bool:
        found = rng.random() < DETECTION.get(condition, 1.0)
        conds.append(Condition(p.patient_id, tooth, condition, severity, found))
        return found

    def propose(tooth: str, code: str, reason: str, priority: str) -> None:
        plan.append(PlanItem(p.patient_id, tooth, code, reason, priority, "proposed"))

    implant_proposed = False

    # which permanent teeth are missing
    missing: dict[int, str] = {}
    if age >= 12:
        for w in WISDOM:
            if age < 17:
                missing[w] = "never_erupted"
            elif rng.random() < (0.35 if age < 25 else 0.75):
                missing[w] = "extracted"
            elif rng.random() < 0.05:
                missing[w] = "congenital"
        candidates = [n for n in range(1, 33) if n not in WISDOM]
        rng.shuffle(candidates)
        # molars are lost first
        candidates.sort(key=lambda n: 0 if n in MOLARS else 1 if n in PREMOLARS else 2)
        for n in candidates[: _missing_count(rng, age, t.income_tier, t.recall_compliance)]:
            missing[n] = "extracted"

    for name in names:
        if not name.isdigit():
            teeth.append(Tooth(p.patient_id, name, "primary", True, "", "none", 0, 0, False))
            if rng.random() < 0.35 * t.caries_risk and add_cond(name, "caries", rng.choice(["early", "moderate"])):
                propose(name, "D2391", "decay on baby tooth", "needed")
            continue

        n = int(name)
        if n in missing:
            replaced = missing[n] == "extracted" and n not in WISDOM and rng.random() < 0.35
            teeth.append(
                Tooth(
                    p.patient_id,
                    name,
                    _kind(n),
                    replaced,
                    missing[n] if not replaced else "",
                    "implant_crown" if replaced else "none",
                    0,
                    rng.randint(1, 15) if replaced else 0,
                    False,
                )
            )
            if missing[n] == "extracted" and n not in WISDOM and not replaced and age >= 18:
                conds.append(Condition(p.patient_id, name, "missing_unreplaced", "", True))
                # offices propose an implant for one gap at a time, and less often for the very old
                if not implant_proposed and age < 80 and rng.random() < 0.07:
                    implant_proposed = True
                    for code in ("D6010", "D6057", "D6065"):
                        propose(name, code, "replace missing tooth", "elective")
            continue

        # existing work, more with age and caries risk
        years_exposed = max(0, age - 6)
        wear = years_exposed / 70
        restoration, surfaces, rest_age, rct = "none", 0, 0, False
        r = rng.random()
        if age >= 25 and r < 0.06 * wear * 3 * (n in MOLARS or n in PREMOLARS):
            restoration, rest_age = "crown", rng.randint(1, min(30, years_exposed))
            rct = rng.random() < 0.55
        elif r < (0.15 + 0.5 * t.caries_risk) * wear * (1.6 if n in MOLARS else 1.0):
            restoration, surfaces = "filling", rng.choice([1, 1, 2, 2, 3])
            rest_age = rng.randint(1, max(1, min(25, years_exposed)))
        teeth.append(Tooth(p.patient_id, name, _kind(n), True, "", restoration, surfaces, rest_age, rct))

        # active problems on this tooth
        if 6 <= age <= 15 and n in FIRST_MOLARS | SECOND_MOLARS and restoration == "none" and rng.random() < 0.3:
            propose(name, "D1351", "sealant on molar", "needed")
        if rng.random() < 0.022 * t.caries_risk * (2 if n in MOLARS else 1):
            sev = rng.choices(["early", "moderate", "deep"], [0.45, 0.35, 0.20])[0]
            if add_cond(name, "caries", sev):
                if sev == "deep":
                    propose(name, _root_canal_code(n), "deep decay into the nerve", "urgent")
                    propose(name, "D2950", "build up after root canal", "urgent")
                    propose(name, "D2740", "crown after root canal", "urgent")
                elif sev == "moderate" and surfaces >= 2:
                    propose(name, "D2740", "large decay under old filling", "needed")
                else:
                    propose(name, _filling_code(n, rng.choice([1, 2]) if sev == "early" else 2), "decay", "needed")
        old_work = restoration in ("filling", "crown") and rest_age >= (12 if restoration == "filling" else 15)
        if old_work and rng.random() < 0.10 and add_cond(name, "failing_restoration", "moderate"):
            propose(name, "D2740", f"failing old {restoration}", "needed")
        big_filling = n in MOLARS | PREMOLARS and restoration == "filling" and surfaces >= 2
        if big_filling and rng.random() < 0.04 and add_cond(name, "cracked", "moderate"):
            propose(name, "D2740", "cracked tooth", "needed")
        if n in WISDOM and 16 <= age <= 30 and rng.random() < 0.07 and add_cond(name, "impacted", ""):
            propose(name, "D7240", "impacted wisdom tooth", "needed")

    # gums
    true_stage = _perio_stage(rng, age, t)
    stages = ["healthy", "gingivitis", "stage_1", "stage_2", "stage_3", "stage_4"]
    charted = true_stage
    if rng.random() < 0.2:  # under- or over-charted by one stage
        i = stages.index(true_stage) + rng.choice([-1, 1])
        charted = stages[max(0, min(5, i))]
    perio = Perio(
        p.patient_id,
        true_stage,
        charted,
        true_stage != "healthy" and rng.random() < 0.8,
        t.grinding and rng.random() < 0.7,
    )
    # many perio patients were already treated and now come for maintenance instead
    already_treated = charted in ("stage_2", "stage_3", "stage_4") and rng.random() < 0.3 + 0.4 * t.recall_compliance
    perio.maintenance = already_treated
    if charted in ("stage_2", "stage_3", "stage_4") and not already_treated:
        present = {int(x.tooth) for x in teeth if x.tooth.isdigit() and x.present and x.restoration != "implant_crown"}
        for q, rng_q in QUADS.items():
            n_teeth = sum(1 for x in rng_q if x in present)
            if n_teeth:
                propose(f"Q{q}", "D4341" if n_teeth >= 4 else "D4342", "scaling and root planing", "needed")
        if charted == "stage_4" and rng.random() < 0.4:
            propose("Q1", "D4260", "perio surgery", "needed")

    # whole-mouth items
    if perio.grinding_wear and age >= 16:
        propose("", "D9944", "grinding wear, protect teeth", "needed")
    if age >= 18 and p.marketing_consent and t.cosmetic_interest > 0.6 and rng.random() < 0.5:
        propose("", "D9972", "asked about whitening", "elective")
    if 16 <= age <= 55 and t.cosmetic_interest > 0.55 and rng.random() < 0.12:
        propose("", "D8090", "crowding, interested in straightening", "elective")
    return teeth, conds, perio, plan


def generate(
    patients: list[Patient], truths: list[Truth], seed: int = 42
) -> tuple[list[Tooth], list[Condition], list[Perio], list[PlanItem]]:
    teeth: list[Tooth] = []
    conds: list[Condition] = []
    perio: list[Perio] = []
    plan: list[PlanItem] = []
    for p, t in zip(patients, truths, strict=True):
        rng = random.Random(f"chart-{p.patient_id}-{seed}")
        a, b, c, d = chart_patient(rng, p, t)
        teeth += a
        conds += b
        perio.append(c)
        plan += d
    return teeth, conds, perio, plan


def write(teeth: list[Tooth], conds: list[Condition], perio: list[Perio], plan: list[PlanItem], out_dir: Path) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    tables: dict[str, list] = {
        "teeth.csv": teeth,
        "conditions.csv": conds,
        "perio.csv": perio,
        "treatment_plan.csv": plan,
    }
    for name, rows in tables.items():
        with (out_dir / name).open("w", newline="") as f:
            dicts = [asdict(r) for r in rows]
            writer = csv.DictWriter(f, fieldnames=list(dicts[0]))
            writer.writeheader()
            writer.writerows(dicts)
    return out_dir
