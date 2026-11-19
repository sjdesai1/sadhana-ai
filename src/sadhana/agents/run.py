"""Run the outreach agent for one office over a stretch of send days, then measure the lift.

For simulated offices the simulator plays the patients: results, replies and opt-outs after each day. For a real
office, results would arrive with the next sync instead.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path

from sqlalchemy import Engine, text

from sadhana.agents import desk, lift
from sadhana.agents.outreach import OutreachAgent
from sadhana.agents.replies import ReplyDesk
from sadhana.config import settings
from sadhana.data.db import office_session, setup_office
from sadhana.messaging.policy import load_settings
from sadhana.office_calendar import DAYS
from sadhana.simulator import responses

OUTREACH_TABLES = (
    "outreach_results", "inbound_messages", "messages", "outreach_targets", "opt_outs", "desk_tasks",
    "conversations", "pending_bookings", "refill_offers",
)  # fmt: skip


@dataclass
class OutreachResult:
    office_id: str
    days: list[date]
    by_status: dict[str, int] = field(default_factory=dict)
    by_channel: dict[str, int] = field(default_factory=dict)
    opt_outs: int = 0
    replies: dict[str, int] = field(default_factory=dict)
    escalations: int = 0
    desk_open: dict[str, int] = field(default_factory=dict)
    estimates: list[dict] = field(default_factory=list)


def send_days(start: date, n: int, allowed: list[str]) -> list[date]:
    out, d = [], start
    while len(out) < n:
        d += timedelta(days=1)
        if DAYS[d.weekday()] in allowed:
            out.append(d)
    return out


def run_outreach(
    engine: Engine, office_id: str, today: date, days: int = 20, sim_dir: Path | None = None, seed: int = 42,
    fresh: bool = True,
) -> OutreachResult:  # fmt: skip
    """`fresh` clears earlier outreach first, so a simulated pilot can be rerun from the same starting point."""
    with engine.begin() as conn:
        setup_office(conn, office_id)
        office = conn.execute(
            text("SELECT config FROM sadhana_meta.offices WHERE office_id = :o"), {"o": office_id}
        ).scalar_one()
        if fresh:
            conn.execute(text("DELETE FROM sadhana_meta.lift_estimates WHERE office_id = :o"), {"o": office_id})
    s = load_settings(settings.configs_dir)
    truth = responses.load_truth(sim_dir) if sim_dir else None
    sim_cfg = responses.load_settings(settings.configs_dir)
    result = OutreachResult(office_id, send_days(today, days, s["send_days"]))
    with office_session(engine, office_id) as conn:
        if fresh:
            for t in OUTREACH_TABLES:
                conn.execute(text(f"DELETE FROM {t}"))
        agent = OutreachAgent(conn, office, s, today)
        replies = ReplyDesk(conn, office, s, today) if truth is not None else None
        for day in result.days:
            for t in agent.run_day(day):
                result.by_status[t.status] = result.by_status.get(t.status, 0) + 1
                if t.channel and t.status == "messaged":
                    result.by_channel[t.channel] = result.by_channel.get(t.channel, 0) + 1
            if truth is not None:
                out = responses.respond(conn, office, s, truth, sim_cfg, day, seed, replies)
                out.update(responses.work_desk(conn, office, s, sim_cfg, day, seed))
                result.opt_outs += out.pop("opt_outs")
                out.pop("results")
                for k, v in out.items():
                    result.replies[k] = result.replies.get(k, 0) + v
            result.escalations += desk.escalate(conn, s, day)
        result.desk_open = {
            f"{kind} at {level}": n
            for kind, level, n in conn.execute(
                text("SELECT kind, level, count(*) FROM desk_tasks WHERE status = 'open' GROUP BY 1, 2 ORDER BY 1, 2")
            )
        }
        result.estimates = lift.measure(conn)
    lift.save(engine, office_id, result.days[-1], result.estimates)
    return result
