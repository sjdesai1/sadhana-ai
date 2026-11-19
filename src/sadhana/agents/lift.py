"""Measuring what outreach changes: success rates of the outreach group against the holdout group.

lift = (outreach rate - holdout rate) / (1 - holdout rate): the share of people who would not have acted on their
own that outreach moved. Same terms as the optimizer's `outreach_lift`, which uses the measured value once the
holdout is big enough, and its own assumption until then.
"""

from __future__ import annotations

import math
from datetime import date

from sqlalchemy import Connection, Engine, insert, text

from sadhana.data.schema import lift_estimates

Row = dict


def measure(conn: Connection) -> list[Row]:
    """Lift and a 95% interval per group, from every target with a result so far (by arm, intention to treat)."""
    rows = conn.execute(
        text(
            "SELECT t.lift_group, t.arm, count(*) AS n, avg(r.success::int) AS rate "
            "FROM outreach_targets t JOIN outreach_results r USING (target_id) GROUP BY t.lift_group, t.arm"
        )
    ).all()
    by: dict[str, dict[str, tuple[int, float]]] = {}
    for group, arm, n, rate in rows:
        by.setdefault(group, {})[arm] = (int(n), float(rate))
    out = []
    for group, arms in sorted(by.items()):
        if "outreach" not in arms or "holdout" not in arms:
            continue
        (n1, p1), (n0, p0) = arms["outreach"], arms["holdout"]
        se = math.sqrt(p1 * (1 - p1) / n1 + p0 * (1 - p0) / n0)
        scale = 1 - p0 if p0 < 1 else 1.0
        out.append(
            {
                "lift_group": group,
                "n_outreach": n1,
                "n_holdout": n0,
                "rate_outreach": round(p1, 4),
                "rate_holdout": round(p0, 4),
                "lift": round((p1 - p0) / scale, 4),
                "ci_low": round((p1 - p0 - 1.96 * se) / scale, 4),
                "ci_high": round((p1 - p0 + 1.96 * se) / scale, 4),
            }
        )
    return out


def save(engine: Engine, office_id: str, as_of: date, estimates: list[Row]) -> None:
    with engine.begin() as conn:
        for e in estimates:
            conn.execute(insert(lift_estimates).values(office_id=office_id, as_of=as_of, **e))


def latest(engine: Engine, office_id: str, min_holdout: int) -> dict[str, float]:
    """The most recent measured lift per group, only where the holdout is big enough to trust."""
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT DISTINCT ON (lift_group) lift_group, lift, n_holdout FROM sadhana_meta.lift_estimates "
                "WHERE office_id = :o ORDER BY lift_group, measured_at DESC, id DESC"
            ),
            {"o": office_id},
        ).all()
    return {g: max(0.0, float(v)) for g, v, n in rows if n >= min_holdout}
