"""Next-best item: the one open treatment item most worth raising with each patient now.

Only items a dentist already diagnosed are candidates, so every suggestion is clinically appropriate.
Cosmetic items need the patient's marketing consent. Each item is valued as
    chance it gets booked x office fee x priority weight
with urgent work weighted up, and the reason the desk sees says why: urgent, benefits that expire Dec 31,
or work the patient already said yes to but never booked. Suggestions go to the office's own outreach and
staff (decision 0007), never straight to the patient as a sales pitch.
"""

from __future__ import annotations

import pandas as pd

COSMETIC = {"D9972", "D8090", "D2962"}
PRIORITY_WEIGHT = {"urgent": 1.5, "needed": 1.0, "elective": 0.8}


def pick(
    plan: pd.DataFrame,
    accept_scores: pd.DataFrame,
    fees: dict[str, float],
    marketing_consent: dict[str, bool],
    benefits_this_year: dict[str, float],
) -> pd.DataFrame:
    """One row per patient with open work: the item to raise, its value and why."""
    chances = accept_scores.rename(columns={"entity_id": "plan_id", "score": "p_book"})[["plan_id", "p_book"]]
    scored = plan.merge(chances, on="plan_id")
    scored = scored[~scored["code"].isin(COSMETIC) | scored["patient_id"].map(marketing_consent).fillna(False)]
    if scored.empty:
        return pd.DataFrame(
            columns=[
                "patient_id",
                "plan_id",
                "code",
                "tooth",
                "p_book",
                "value",
                "expected_value",
                "benefits_this_year",
                "reason",
            ]
        )
    scored = scored.assign(
        value=scored["code"].map(fees).fillna(0.0),
        weight=scored["priority"].map(PRIORITY_WEIGHT).fillna(1.0),
    )
    scored["expected_value"] = (scored["p_book"] * scored["value"] * scored["weight"]).round(2)
    best = scored.sort_values(["patient_id", "expected_value"], ascending=[True, False]).drop_duplicates("patient_id")
    best = best.assign(benefits_this_year=best["patient_id"].map(benefits_this_year).fillna(0.0))

    def reason(r: pd.Series) -> str:
        parts = []
        if r["priority"] == "urgent":
            parts.append("urgent")
        if r["status"] == "accepted":
            parts.append("already said yes, not booked")
        if r["benefits_this_year"] > 0:
            parts.append(f"${r['benefits_this_year']:,.0f} of insurance expires Dec 31")
        return "; ".join(parts) or "diagnosed, not booked"

    best["reason"] = best.apply(reason, axis=1)
    cols = [
        "patient_id",
        "plan_id",
        "code",
        "tooth",
        "p_book",
        "value",
        "expected_value",
        "benefits_this_year",
        "reason",
    ]
    return best[cols].sort_values("expected_value", ascending=False).reset_index(drop=True)
