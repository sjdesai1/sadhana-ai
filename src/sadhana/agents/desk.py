"""The front desk queue (decision 0007).

- needs_reply: a patient asked something the agent cannot answer inside its templates
- call: someone to phone (no text or email consent, or no openings to offer)
- approval: an action that waits for a person (cosmetic follow-up waits for the dentist)
- fyi: done by the agent, for the record (booked, confirmed, cancelled, declined)

Anything that needs a person escalates when its time at a level runs out: front desk, then office manager, then
dentist. FYI items never escalate.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from sqlalchemy import Connection, insert, text

from sadhana.data.schema import desk_tasks

Row = dict[str, Any]


def add(
    conn: Connection, office_id: str, settings: Row, kind: str, patient_id: str, summary: str, day: date,
    contact_id: str = "", ref_id: str = "", level: str = "front_desk",
) -> None:  # fmt: skip
    days = settings["escalation"]["days_at_level"]
    conn.execute(
        insert(desk_tasks).values(
            office_id=office_id, kind=kind, patient_id=patient_id, contact_id=contact_id or patient_id,
            summary=summary, ref_id=ref_id, level=level, created_on=day,
            due_on=None if kind == "fyi" else day + timedelta(days=days[level]),
            status="done" if kind == "fyi" else "open", resolved_on=day if kind == "fyi" else None, escalations=0,
        )
    )  # fmt: skip


def escalate(conn: Connection, settings: Row, today: date) -> int:
    """Move overdue open items up a level; returns how many moved."""
    cfg = settings["escalation"]
    levels = cfg["levels"]
    rows = conn.execute(
        text("SELECT task_id, level FROM desk_tasks WHERE status = 'open' AND kind <> 'fyi' AND due_on < :t"),
        {"t": today},
    ).all()
    for task_id, level in rows:
        nxt = levels[min(levels.index(level) + 1, len(levels) - 1)]
        conn.execute(
            text("UPDATE desk_tasks SET level = :l, due_on = :d, escalations = escalations + 1 WHERE task_id = :i"),
            {"l": nxt, "d": today + timedelta(days=cfg["days_at_level"][nxt]), "i": task_id},
        )
    return len(rows)


def resolve(conn: Connection, task_id: int, day: date) -> bool:
    n = conn.execute(
        text("UPDATE desk_tasks SET status = 'done', resolved_on = :d WHERE task_id = :i AND status = 'open'"),
        {"d": day, "i": task_id},
    ).rowcount
    return bool(n)
