"""Build and save one office's slot plan and at-risk list from the latest scores (make models first)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any

import pandas as pd
from sqlalchemy import Connection, Engine, insert, text

from sadhana.agents import lift
from sadhana.config import settings
from sadhana.data.db import office_session, setup_office
from sadhana.data.schema import at_risk_visits, slot_plan
from sadhana.models import features
from sadhana.optimizer.plan import AtRisk, Fill, Planner, as_rows


@dataclass
class PlanResult:
    office_id: str
    today: date
    open_blocks: int
    open_hours: float
    fills: list[Fill] = field(default_factory=list)
    at_risk: list[AtRisk] = field(default_factory=list)
    outreach_lift: dict[str, float] = field(default_factory=dict)
    lift_source: dict[str, str] = field(default_factory=dict)

    @property
    def expected_value(self) -> float:
        return round(sum(f.expected_value for f in self.fills), 2)

    def summary(self) -> dict:
        by_column: dict[str, int] = {}
        for f in self.fills:
            by_column[f.column] = by_column.get(f.column, 0) + 1
        return {
            "as_of": self.today.isoformat(),
            "open_blocks": self.open_blocks,
            "open_hours": round(self.open_hours, 1),
            "filled_blocks": len(self.fills),
            "filled_by_column": by_column,
            "expected_value": self.expected_value,
            "at_risk_visits": len(self.at_risk),
            "value_at_risk": round(sum(a.value_at_risk for a in self.at_risk), 2),
            "outreach_lift": self.outreach_lift,
            "lift_source": self.lift_source,
        }


def _scores(conn: Connection, model: str) -> pd.DataFrame:
    rows = conn.execute(text("SELECT entity_id, patient_id, score FROM scores WHERE model = :m"), {"m": model}).all()
    return pd.DataFrame(rows, columns=["entity_id", "patient_id", "score"]).astype({"score": float})


def gather(conn: Connection, today: date, end: date) -> dict[str, Any]:
    """What the planner reads: booked visits up to `end`, who already has something booked, miss rates, scores."""
    data = features.load(conn)
    booked = pd.DataFrame(
        conn.execute(
            text(
                'SELECT a.appt_id, a.patient_id, a.provider_id, a."column", a.date, '
                "to_char(a.start, 'HH24:MI') AS start, to_char(a.\"end\", 'HH24:MI') AS end, a.codes, p.payer "
                "FROM appointments a LEFT JOIN patients p USING (patient_id) "
                "WHERE a.status = 'scheduled' AND a.date > :t AND a.date <= :e ORDER BY a.date, a.start"
            ),
            {"t": today, "e": end},
        ).all(),
        columns=["appt_id", "patient_id", "provider_id", "column", "date", "start", "end", "codes", "payer"],
    )
    future = {
        r[0]
        for r in conn.execute(
            text("SELECT DISTINCT patient_id FROM appointments WHERE status = 'scheduled' AND date > :t"),
            {"t": today},
        )
    }
    miss = {
        pid: (ns + lc) / (done + ns + lc + 2)
        for pid, done, ns, lc in conn.execute(
            text(
                "SELECT patient_id, coalesce(completed_visits, 0), coalesce(no_shows, 0), "
                "coalesce(late_cancels, 0) FROM patient_status"
            )
        )
    }
    return {
        "patients": data["patients"],
        "plan": data["plan"],
        "booked": booked,
        "future": future,
        "miss": miss,
        "recall": _scores(conn, "recall_self_book"),
        "accept": _scores(conn, "treatment_accept"),
        "missed": dict(_scores(conn, "missed_visit")[["entity_id", "score"]].itertuples(index=False)),
    }


def plan_office(engine: Engine, office_id: str, today: date) -> PlanResult:
    with engine.begin() as conn:
        setup_office(conn, office_id)
        office = conn.execute(
            text("SELECT config FROM sadhana_meta.offices WHERE office_id = :o"), {"o": office_id}
        ).scalar_one()
    planner = Planner(office, settings.configs_dir, today)
    measured = lift.latest(engine, office_id, planner.cfg["min_holdout_for_lift"])
    planner.cfg["outreach_lift"] = {**planner.cfg["outreach_lift"], **measured}
    planner.lift_source = {k: ("measured" if k in measured else "assumed") for k in planner.cfg["outreach_lift"]}
    end = today + timedelta(days=planner.cfg["horizon_days"])
    with office_session(engine, office_id) as conn:
        g = gather(conn, today, end)
    booked, missed = g["booked"], g["missed"]
    cands = planner.candidates(g["patients"], g["recall"], g["plan"], g["accept"], g["miss"], g["future"])
    blocks = planner.blocks(booked)
    fills = planner.assign(blocks, cands)
    used = {f.patient_id for f in fills}
    risky = planner.at_risk(booked, missed, cands, used)
    result = PlanResult(
        office_id, today, len(blocks), sum(b["minutes"] for b in blocks) / 60, fills, risky,
        dict(planner.cfg["outreach_lift"]), planner.lift_source,
    )  # fmt: skip

    with office_session(engine, office_id) as conn:
        conn.execute(text("DELETE FROM slot_plan"))
        conn.execute(text("DELETE FROM at_risk_visits"))
        if fills:
            conn.execute(insert(slot_plan), [{"office_id": office_id, **r} for r in as_rows(fills)])
        if risky:
            conn.execute(insert(at_risk_visits), [{"office_id": office_id, **r} for r in as_rows(risky)])
    return result
