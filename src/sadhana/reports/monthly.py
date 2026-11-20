"""The monthly office report: what Sadhana recovered, what it cost, how the schedule did, and what is next.

Recovered is counted only where it can be shown, never as everything booked by text:
- Outreach (recall, treatment follow-up, confirmations): the measured difference between the outreach group and
  the holdout group, times the people reached, times the value of a booking (or a kept visit). People who would
  have booked anyway are not counted
- Refills: slots freed by cancellations and filled by the agent, less the share the desk would have refilled
  anyway (an assumption until measured)
Value is what the office collects: for a cleaning visit, its average collected per hygiene visit over the last
year; for treatment, the expected collected for that code across its payers. Margin is shown next to it.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import Connection, Engine, text

from sadhana.config import settings
from sadhana.data.db import office_session
from sadhana.economics import observed
from sadhana.economics.engine import build, inputs_for
from sadhana.office_calendar import Calendar, holidays
from sadhana.optimizer.horizon import _appointments, forecast_office

Row = dict[str, Any]


def load_settings(configs_dir: Path) -> Row:
    return json.loads((configs_dir / "reports.json").read_text())


@dataclass
class Report:
    office_id: str
    office_name: str
    period: tuple[date, date]
    fee: float
    recovered: list[Row] = field(default_factory=list)  # one row per source
    schedule: Row = field(default_factory=dict)
    outreach: Row = field(default_factory=dict)
    desk: Row = field(default_factory=dict)
    insurance: Row = field(default_factory=dict)
    next_90: Row = field(default_factory=dict)
    segments: list[Row] = field(default_factory=list)  # outreach lift for new, established and lapsed patients
    notes: list[str] = field(default_factory=list)

    @property
    def recovered_total(self) -> float:
        return round(sum(r["collected"] for r in self.recovered), 2)

    @property
    def recovered_range(self) -> tuple[float, float]:
        """The 95% range on recovered, from the ranges on each measured lift."""
        return (round(sum(r["collected_low"] for r in self.recovered), 2),
                round(sum(r["collected_high"] for r in self.recovered), 2))  # fmt: skip

    @property
    def margin_total(self) -> float:
        return round(sum(r["margin"] for r in self.recovered), 2)

    @property
    def roi(self) -> float:
        return round(self.recovered_total / self.fee, 1) if self.fee else 0.0

    def as_dict(self) -> Row:
        return {
            "office_id": self.office_id,
            "office_name": self.office_name,
            "period": [self.period[0].isoformat(), self.period[1].isoformat()],
            "fee": self.fee,
            "recovered_total": self.recovered_total,
            "recovered_range": list(self.recovered_range),
            "margin_total": self.margin_total,
            "return_on_fee": self.roi,
            "recovered": self.recovered,
            "schedule": self.schedule,
            "outreach": self.outreach,
            "desk": self.desk,
            "insurance": self.insurance,
            "next_90": self.next_90,
            "segments": self.segments,
            "notes": self.notes,
        }


def _values(conn: Connection, office: Row, today: date) -> tuple[dict[str, tuple[float, float]], float, float]:
    """(collected, margin) per treatment code; collected and margin per hygiene visit."""
    econ = build(inputs_for(office, settings.configs_dir, observed.load(conn)))
    mix = office["payer_mix"]
    per_code: dict[str, list[float]] = {}
    for r in econ.detail:
        w = mix.get(r["payer"], 0.0)
        acc = per_code.setdefault(r["code"], [0.0, 0.0, 0.0])
        acc[0] += w * r["collected"]
        acc[1] += w * r["net_margin"]
        acc[2] += w
    values = {c: (a[0] / a[2], a[1] / a[2]) for c, a in per_code.items() if a[2]}
    hyg = conn.execute(
        text(
            "SELECT avg(paid) FROM (SELECT a.appt_id, sum(coalesce(l.primary_paid, 0) + coalesce(l.secondary_paid, 0) "
            "+ coalesce(l.patient_portion, 0)) AS paid FROM appointments a JOIN ledger l USING (appt_id) "
            "WHERE a.\"column\" = 'hygiene' AND a.status = 'completed' AND a.date > :d GROUP BY a.appt_id) x"
        ),
        {"d": today - timedelta(days=365)},
    ).scalar()
    hyg_collected = float(hyg or 0.0)
    cleaning = [values[c] for c in ("D1110", "D0120", "D0274") if c in values]
    cleaning_margin_share = sum(m for _, m in cleaning) / sum(c for c, _ in cleaning) if cleaning else 0.4
    return values, hyg_collected, hyg_collected * cleaning_margin_share


def _schedule(engine: Engine, office: Row, start: date) -> Row:
    """Chair use and missed visits for the month before the report period, against the month before that."""
    cal = Calendar(office, holidays(range(start.year - 2, start.year + 1)))
    with office_session(engine, office["office_id"]) as conn:
        appts = _appointments(conn)

    def month(m_start: date, m_end: date) -> Row:
        cap = 0.0
        d = m_start
        while d < m_end:
            for column in ("hygiene", "doctor", "specialist"):
                cap += cal.capacity(d, column) / 60
            d += timedelta(days=1)
        a = appts[(appts["date"] >= m_start) & (appts["date"] < m_end)]
        done = float(a.loc[a["status"] == "completed", "hours"].sum())
        due = a[a["status"].isin(("completed", "no_show", "late_cancel"))]
        missed = int(due["status"].isin(("no_show", "late_cancel")).sum())
        return {"chair_use": round(done / cap, 3) if cap else 0.0, "visits": len(due), "missed": missed,
                "missed_rate": round(missed / len(due), 3) if len(due) else 0.0}  # fmt: skip

    last_end = start.replace(day=1)
    last_start = (last_end - timedelta(days=1)).replace(day=1)
    prior_start = (last_start - timedelta(days=1)).replace(day=1)
    return {"month": last_start.strftime("%B %Y"), "last_month": month(last_start, last_end),
            "month_before": month(prior_start, last_start)}  # fmt: skip


def build_report(engine: Engine, office_id: str, today: date) -> Report:
    cfg = load_settings(settings.configs_dir)
    with engine.connect() as conn:
        office = conn.execute(
            text("SELECT config FROM sadhana_meta.offices WHERE office_id = :o"), {"o": office_id}
        ).scalar_one()
        lifts = {
            r.lift_group: r
            for r in conn.execute(
                text(
                    "SELECT DISTINCT ON (lift_group) lift_group, n_outreach, n_holdout, rate_outreach, rate_holdout, "
                    "lift, ci_low, ci_high FROM sadhana_meta.lift_estimates WHERE office_id = :o AND segment IS NULL "
                    "ORDER BY lift_group, measured_at DESC, id DESC"
                ),
                {"o": office_id},
            )
        }
    with office_session(engine, office_id) as conn:
        span = conn.execute(text("SELECT min(targeted_on), max(targeted_on) FROM outreach_targets")).one()
        start, end = (span[0], span[1]) if span[0] else (today, today)
        values, hyg_collected, hyg_margin = _values(conn, office, today)
        report = Report(office_id, office.get("name", office_id), (start, end), float(cfg["monthly_fee"]))
        kept = conn.execute(
            text(
                "SELECT avg((status = 'completed')::int) FROM appointments WHERE date > :d AND date <= :t "
                "AND status IN ('completed', 'no_show', 'late_cancel')"
            ),
            {"d": today - timedelta(days=365), "t": today},
        ).scalar()
        show = float(kept or 0.88)  # a booking is worth something only if the patient comes

        # recovered: outreach, by group, from the measured difference against the holdout
        for group, label in (("recall", "Recall outreach"), ("treatment", "Treatment follow-up"),
                             ("confirm", "Confirmations of at-risk visits")):  # fmt: skip
            est = lifts.get(group)
            targets = conn.execute(
                text("SELECT code FROM outreach_targets WHERE arm = 'outreach' AND lift_group = :g ORDER BY target_id"),
                {"g": group},
            ).all()
            if est is None or not targets:
                continue
            extra = max(0.0, float(est.rate_outreach) - float(est.rate_holdout)) * len(targets)
            if group != "confirm":  # confirmations are measured on kept visits already
                extra *= show
            if group == "treatment":
                vals = [values.get(c, (0.0, 0.0)) for (c,) in targets]
                coll = sum(v[0] for v in vals) / len(vals)
                marg = sum(v[1] for v in vals) / len(vals)
            else:
                coll, marg = hyg_collected, hyg_margin
            # the 95% range on the lift, in the same terms: extra visits at the low and high end
            scale = (1 - float(est.rate_holdout)) * len(targets) * (show if group != "confirm" else 1.0)
            low, high = max(0.0, float(est.ci_low)) * scale, max(0.0, float(est.ci_high)) * scale
            report.recovered.append(
                {"source": label, "measured": True, "people_reached": len(targets),
                 "rate_with_outreach": float(est.rate_outreach), "rate_holdout": float(est.rate_holdout),
                 "extra_bookings": round(extra, 1), "collected": round(extra * coll, 2),
                 "collected_low": round(low * coll, 2), "collected_high": round(high * coll, 2),
                 "margin": round(extra * marg, 2),
                 "interval": [round(float(est.ci_low), 3), round(float(est.ci_high), 3)]}
            )  # fmt: skip
            if est.n_holdout < 100:
                report.notes.append(
                    f"{label}: the holdout group is still small ({est.n_holdout}), so this figure will move as more "
                    "months come in."
                )
        # recovered: refills, less what the desk would have filled anyway
        refills = conn.execute(text("SELECT code FROM refill_offers WHERE status = 'won' ORDER BY offer_id")).all()
        if refills:
            share = (1 - cfg["refill_without_sadhana"]) * show
            coll = sum(values.get(c, (hyg_collected, hyg_margin))[0] for (c,) in refills) * share
            marg = sum(values.get(c, (hyg_collected, hyg_margin))[1] for (c,) in refills) * share
            report.recovered.append(
                {"source": "Cancelled slots refilled", "measured": False, "people_reached": len(refills),
                 "extra_bookings": round(len(refills) * share, 1), "collected": round(coll, 2),
                 "collected_low": round(coll, 2), "collected_high": round(coll, 2), "margin": round(marg, 2)}
            )  # fmt: skip
            report.notes.append(
                f"Refills count {1 - cfg['refill_without_sadhana']:.0%} of their value: the desk might have filled "
                "the rest itself. That share is an assumption until it is measured."
            )

        report.notes.append(
            f"Extra visits count only patients who come: {show:.0%} of booked visits are kept here. The first month "
            "also works through the backlog of overdue patients, so later months will be smaller."
        )
        q = lambda sql, **p: conn.execute(text(sql), p).scalar() or 0  # noqa: E731
        by_channel = dict(
            conn.execute(
                text("SELECT channel, count(*) FROM messages WHERE kind <> 'reply' GROUP BY channel ORDER BY channel")
            ).all()
        )
        sent = sum(by_channel.values())
        replies = q("SELECT count(*) FROM inbound_messages")
        handled = q("SELECT count(*) FROM messages WHERE kind = 'reply'")
        booked = q("SELECT count(*) FROM pending_bookings WHERE kind = 'book'")
        opt_outs = q("SELECT count(*) FROM opt_outs")
        confirmed = q("SELECT count(*) FROM desk_tasks WHERE kind = 'fyi' AND summary LIKE 'Confirmed%'")
        report.outreach = {"messages": sent, "by_channel": by_channel, "replies": replies, "booked_by_text": booked,
                           "opt_outs": opt_outs, "opt_out_rate": round(opt_outs / sent, 4) if sent else 0.0,
                           "held_out": q("SELECT count(*) FROM outreach_targets WHERE arm = 'holdout'")}  # fmt: skip
        m = cfg["minutes_saved"]
        minutes = handled * m["reply_handled"] + sent * m["message_sent"] + confirmed * m["confirmation"]
        report.desk = {
            "replies_handled": handled,
            "hours_saved": round(minutes / 60, 1),
            "tasks": q("SELECT count(*) FROM desk_tasks WHERE kind <> 'fyi'"),
            "tasks_done": q("SELECT count(*) FROM desk_tasks WHERE kind <> 'fyi' AND status = 'done'"),
            "escalated": q("SELECT count(*) FROM desk_tasks WHERE escalations > 0"),
            "still_open": q("SELECT count(*) FROM desk_tasks WHERE status = 'open'"),
        }
        flagged = [
            dict(r)
            for r in conn.execute(
                text("SELECT payer, paid_vs_schedule, changed_on FROM payer_schedule_check WHERE flag ORDER BY payer")
            ).mappings()
        ]
        benefits = conn.execute(
            text("SELECT coalesce(sum(benefits_this_year), 0), count(*) FROM next_best WHERE benefits_this_year > 0")
        ).one()
        report.insurance = {
            "payers_under_schedule": [
                {
                    "payer": f["payer"],
                    "paid_vs_schedule": float(f["paid_vs_schedule"]),
                    "changed_on": f["changed_on"].isoformat() if f["changed_on"] else None,
                }
                for f in flagged
            ],  # fmt: skip
            "benefits_resetting_dec_31": round(float(benefits[0]), 2),
            "patients_with_benefits_left": int(benefits[1]),
        }
    with engine.connect() as conn:
        report.segments = [
            {
                k: (float(v) if k not in ("lift_group", "segment", "n_outreach", "n_holdout") else v)
                for k, v in r.items()
            }
            for r in conn.execute(
                text(
                    "SELECT DISTINCT ON (lift_group, segment) lift_group, segment, n_outreach, n_holdout, "
                    "rate_outreach, rate_holdout, lift, ci_low, ci_high FROM sadhana_meta.lift_estimates "
                    "WHERE office_id = :o AND segment IS NOT NULL "
                    "ORDER BY lift_group, segment, measured_at DESC, id DESC"
                ),
                {"o": office_id},
            ).mappings()
        ]
    report.schedule = _schedule(engine, office, start)
    plan = forecast_office(engine, office_id, today).summary()
    report.next_90 = {k: plan[k] for k in ("utilization", "utilization_with_outreach", "outreach_value",
                                           "unfilled_hours", "emptiest_weeks", "expected_value")}  # fmt: skip
    return report
