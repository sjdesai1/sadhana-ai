"""Simulated patients answering outreach, from their hidden traits (decision 0006: the product never sees them).

For every target, outreach group or holdout, the chance of acting in the window with no outreach comes from
hidden traits (recall compliance, trust, price sensitivity, no-show and late-cancel rates). Outreach moves a share
of the people who would not have acted, depending on the channel, how responsive and trusting the person is, and
how price sensitive they are for treatment. Texts can also draw a STOP, more likely the more messages someone has
had lately; replies and opt-outs go through the product's own inbound handler.

`true_lift` gives the answer the measurement should find, for tests.
"""

from __future__ import annotations

import csv
import json
import random
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import Connection, insert, text

from sadhana.data.schema import outreach_results
from sadhana.messaging import inbound

Row = dict[str, Any]


def load_truth(sim_dir: Path) -> dict[str, Row]:
    with (sim_dir / "truth.csv").open(newline="") as f:
        return {r["patient_id"]: {k: _num(v) for k, v in r.items()} for r in csv.DictReader(f)}


def _num(v: str) -> Any:
    try:
        return float(v)
    except ValueError:
        return v


def load_settings(configs_dir: Path) -> Row:
    return json.loads((configs_dir / "simulator" / "responses.json").read_text())


def chances(t: Row, purpose: str, group: str, channel: str, cfg: Row) -> tuple[float, float]:
    """(chance of success with no outreach, chance with this outreach) for one person."""
    nat, lift = cfg["natural"], cfg["lift"]
    if group == "confirm":
        miss = min(0.9, t["no_show_rate"] + t["late_cancel_rate"])
        base = 1 - miss
        if not channel:
            return base, base
        cut = lift["confirm_effect"] * lift["channel"][channel] * (0.4 + t["text_responsiveness"])
        return base, 1 - miss * max(0.0, 1 - min(cut, 0.9))
    if group == "recall":
        base = t["recall_compliance"] * nat["recall_scale"]
        effect = lift["recall"]
    else:
        base = t["trust_baseline"] * (1 - t["price_sensitivity"] / 2) * nat["treatment_scale"]
        effect = lift["treatment"] * (1.4 - t["price_sensitivity"])
    if not channel:
        return base, base
    effect *= lift["channel"][channel] * (0.5 + t["text_responsiveness"]) * (0.6 + 0.8 * t["trust_baseline"])
    if purpose == "slot_offer":
        effect *= lift["slot_offer_factor"]
    effect = min(effect, 0.9)
    return base, base + (1 - base) * effect


def respond(
    conn: Connection, office: Row, msg_settings: Row, truth: dict[str, Row], cfg: Row, day: date, seed: int
) -> Row:
    """Results for every target picked on `day`, plus replies and opt-outs for the messages sent that day."""
    rng = random.Random(f"{seed}|{office['office_id']}|{day}")
    targets = (
        conn.execute(
            text(
                "SELECT t.target_id, t.patient_id, t.purpose, t.lift_group, t.status, m.channel, m.contact_id, "
                "c.language FROM outreach_targets t "
                "LEFT JOIN messages m USING (target_id) LEFT JOIN patients c ON c.patient_id = m.contact_id "
                "WHERE t.targeted_on = :d ORDER BY t.target_id"
            ),
            {"d": day},
        )
        .mappings()
        .all()
    )
    recent = dict(
        conn.execute(
            text("SELECT contact_id, count(*) FROM messages WHERE sent_on > :d AND sent_on < :t GROUP BY contact_id"),
            {"d": day - timedelta(days=30), "t": day},
        ).all()
    )
    rows, stops = [], 0
    for r in targets:
        t = truth.get(r["patient_id"])
        if t is None:
            continue
        channel = r["channel"] or ("desk_call" if r["status"] == "desk_call" else "")
        base, p = chances(t, r["purpose"], r["lift_group"], channel, cfg)
        success = rng.random() < p
        rows.append(
            {
                "office_id": office["office_id"],
                "target_id": r["target_id"],
                "patient_id": r["patient_id"],
                "success": success,
                "observed_on": day + timedelta(days=cfg["window_days"]),
            }
        )
        if r["channel"] not in ("sms", "email"):
            continue
        contact = truth.get(r["contact_id"], t)
        o = cfg["opt_out"]
        if r["channel"] == "sms":
            p_stop = o["sms_base"] * (1 + o["per_recent_message"] * recent.get(r["contact_id"], 0))
            p_stop *= 1.6 - contact["trust_baseline"]
        else:
            p_stop = o["email_base"]
        if rng.random() < p_stop:
            body = (
                rng.choice(["STOP", "stop", "Unsubscribe", "Please stop texting me"])
                if r["channel"] == "sms"
                else "unsubscribe"
            )
            inbound.handle(conn, office, msg_settings, r["contact_id"], r["channel"], body, day, r["language"])
            stops += 1
        elif r["channel"] == "sms" and rng.random() < contact["text_responsiveness"]:
            if success:
                body = "C" if r["purpose"] == "confirm" else "Yes"
            elif rng.random() < cfg["reply_when_not_booking"]:
                body = "Not right now, thanks"
            else:
                continue
            inbound.handle(conn, office, msg_settings, r["contact_id"], "sms", body, day, r["language"])
    if rows:
        conn.execute(insert(outreach_results), rows)
    return {"results": len(rows), "opt_outs": stops}


def true_lift(conn: Connection, truth: dict[str, Row], cfg: Row, group: str) -> float:
    """The lift the holdout comparison should find for one group: the outreach group's chances with and without
    the outreach they actually got, in the same terms as the estimate."""
    rows = conn.execute(
        text(
            "SELECT t.patient_id, t.purpose, t.status, m.channel FROM outreach_targets t "
            "LEFT JOIN messages m USING (target_id) WHERE t.arm = 'outreach' AND t.lift_group = :g"
        ),
        {"g": group},
    ).all()
    base = with_ = 0.0
    n = 0
    for pid, purpose, status, channel in rows:
        if pid not in truth:
            continue
        ch = channel or ("desk_call" if status == "desk_call" else "")
        b, p = chances(truth[pid], purpose, group, ch, cfg)
        base, with_, n = base + b, with_ + p, n + 1
    base, with_ = base / n, with_ / n
    return (with_ - base) / (1 - base)
