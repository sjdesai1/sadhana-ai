"""The scorecard. For each office and scenario in `configs/evals.json`:

1. Simulate it (hidden truth stays in the simulator's files) and sync it like a PMS
2. Run the whole product: models, slot plan, a simulated outreach pilot with replies and refills, the 90-day plan
3. Grade it against the hidden truth and the rules:
   - data: no hidden truth reached the product's tables
   - models: AUC and calibration on months they never saw; the missed-visit model against the best any model
     could do (ranking by each patient's hidden miss rate); recall scores against who has really lapsed;
     no protected inputs (language, zip, neighborhood, sex)
   - optimizer: the plan against a first-come desk; nobody double-booked
   - outreach: the measured lift's interval covers the true lift; no message after a STOP, without consent, over
     the caps or to the holdout group; the opt-out rate
   - replies and refill: no double booking; one winner per refilled slot
   - forecast: the 13-week baseline made on a past date against what really happened
4. Pass or fail each check against its threshold

Hidden test offices are graded like the rest and never used to tune anything (decision 0008).
"""

from __future__ import annotations

import json
import operator
from dataclasses import asdict, dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from sklearn.metrics import roc_auc_score
from sqlalchemy import Engine, text

from sadhana.agents.run import OUTREACH_TABLES, run_outreach
from sadhana.config import settings
from sadhana.data.db import office_session
from sadhana.models import features as F
from sadhana.models.run import run_office
from sadhana.optimizer.horizon import backtest, forecast_office
from sadhana.optimizer.run import plan_office
from sadhana.simulator import responses
from sadhana.simulator.build import Sim, simulate, write
from sadhana.simulator.population import SIM_TODAY
from sadhana.sync.adapters import SimulatorAdapter
from sadhana.sync.run import sync_office

Row = dict[str, Any]
OPS = {">=": operator.ge, "<=": operator.le, "==": operator.eq}
PROTECTED = ("language", "zip", "neighborhood", "sex")
TRUTH_FIELDS = ("no_show_rate", "late_cancel_rate", "recall_compliance", "text_responsiveness", "trust_baseline",
                "price_sensitivity", "cosmetic_interest", "tech_comfort", "anxiety", "lapsed_on", "cause")  # fmt: skip


INFO_ROWS = [
    "missed_visit_rate", "plan_offers", "plan_expected_value", "overbook_slots", "messages", "opt_outs",
    "booked_by_text", "refilled", "recall_lift_true", "recall_lift_measured", "treatment_lift_true",
    "treatment_lift_measured", "utilization_90d", "utilization_90d_with_outreach", "outreach_value_90d",
]  # fmt: skip


def load_settings(configs_dir: Path) -> Row:
    return json.loads((configs_dir / "evals.json").read_text())


@dataclass
class Check:
    name: str
    value: float | None
    op: str
    threshold: float
    status: str  # pass, fail, n/a


def grade(metrics: Row, checks: Row) -> list[Check]:
    out = []
    for name, (op, threshold) in checks.items():
        v = metrics.get(name)
        if v is None or (isinstance(v, float) and np.isnan(v)):
            out.append(Check(name, None, op, threshold, "n/a"))
            continue
        out.append(Check(name, round(float(v), 4), op, threshold, "pass" if OPS[op](v, threshold) else "fail"))
    return out


class EvalAdapter(SimulatorAdapter):
    """A scenario run synced under its own name, with its base office's settings."""

    def __init__(self, label: str, base_office: str, sim_dir: Path, configs_dir: Path, note: str = "") -> None:
        super().__init__(label, sim_dir, configs_dir)
        self.base_office, self.note = base_office, note

    def office(self) -> Row:
        base = SimulatorAdapter(self.base_office, self.sim_dir, self.configs_dir).office()
        name = base.get("name", self.base_office)
        return {**base, "office_id": self.office_id, "name": f"{name} ({self.note})" if self.note else name}


def _overlaps(conn: Any, include_plan: bool) -> int:
    plan = (
        "UNION ALL SELECT provider_id, date, start, start + make_interval(mins => minutes) FROM slot_plan "
        if include_plan
        else ""
    )
    return int(
        conn.execute(
            text(
                "WITH x AS (SELECT provider_id, date, start, start + make_interval(mins => minutes) e FROM "
                f"pending_bookings WHERE kind = 'book' {plan}UNION ALL SELECT provider_id, date, start, \"end\" "
                "FROM appointments WHERE status = 'scheduled' AND appt_id NOT IN (SELECT appt_id FROM "
                "pending_bookings WHERE kind = 'cancel' AND appt_id IS NOT NULL)) SELECT count(*) FROM x a JOIN x b "
                "ON a.provider_id = b.provider_id AND a.date = b.date AND a.start < b.start AND a.e > b.start"
            )
        ).scalar()
    )


def _hidden_columns(engine: Engine, label: str) -> int:
    with engine.connect() as conn:
        cols = {
            r[0]
            for r in conn.execute(
                text("SELECT column_name FROM information_schema.columns WHERE table_schema = :s"),
                {"s": f"office_{label}"},
            )
        }
    return len(cols & set(TRUTH_FIELDS))


def _model_metrics(engine: Engine, label: str, sim: Sim, m: Row) -> None:
    results = {r.model: r for r in run_office(engine, label, SIM_TODAY)}
    for name, r in results.items():
        m[f"models.{name}.auc"] = r.auc
        m[f"models.{name}.calibration_error"] = r.calibration_error
    truth = {t.patient_id: t.no_show_rate + t.late_cancel_rate for t in sim.truths}
    with office_session(engine, label) as conn:
        rows = F.missed_visit_frame(F.load(conn))
    test = rows[rows["missed"].notna() & (rows["date"] >= pd.Timestamp(SIM_TODAY) - pd.DateOffset(months=4))]
    test = test[test["date"] < pd.Timestamp(SIM_TODAY)]
    best = roc_auc_score(test["missed"], test["patient_id"].map(truth).fillna(np.mean(list(truth.values()))))
    m["models.missed_visit.best_possible_auc"] = best
    m["models.missed_visit.gap_to_best"] = best - results["missed_visit"].auc
    lapsed = {f.patient_id for f in sim.flows if f.lapsed_on and f.lapsed_on < SIM_TODAY.isoformat()}
    recall = results["recall_self_book"].scores
    if recall["patient_id"].isin(lapsed).nunique() == 2:
        m["models.recall_self_book.lapsed_auc"] = roc_auc_score(recall["patient_id"].isin(lapsed), -recall["score"])
    feats = F.MISSED_FEATURES + F.RECALL_FEATURES + F.TREATMENT_FEATURES
    m["models.protected_inputs"] = sum(1 for f in feats if any(p in f for p in PROTECTED))


def _outreach_metrics(engine: Engine, label: str, sim_dir: Path, days: int, m: Row, info: Row) -> None:
    r = run_outreach(engine, label, SIM_TODAY, days, sim_dir)
    truth = responses.load_truth(sim_dir)
    cfg = responses.load_settings(settings.configs_dir)
    est = {e["lift_group"]: e for e in r.estimates}
    with office_session(engine, label) as conn:
        for group in ("recall", "treatment"):
            if group in est:
                true = responses.true_lift(conn, truth, cfg, group)
                info[f"{group}_lift_true"] = round(true, 4)
                info[f"{group}_lift_measured"] = est[group]["lift"]
                info[f"{group}_lift_interval"] = [est[group]["ci_low"], est[group]["ci_high"]]
                se = (est[group]["ci_high"] - est[group]["ci_low"]) / (2 * 1.96)
                info[f"{group}_lift_covered"] = bool(est[group]["ci_low"] <= true <= est[group]["ci_high"])
                m[f"outreach.{group}_lift_error_in_se"] = abs(est[group]["lift"] - true) / se if se else None
        q = lambda sql: int(conn.execute(text(sql)).scalar() or 0)  # noqa: E731
        violations = {
            "after_stop": q(
                "SELECT count(*) FROM messages m JOIN opt_outs o ON o.contact_id = m.contact_id AND o.channel = "
                "m.channel AND m.sent_on > o.opted_out_on AND m.body NOT LIKE '%unsubscribed%'"
            ),
            "no_consent": q(
                "SELECT count(*) FROM messages m JOIN patients c ON c.patient_id = m.contact_id WHERE m.kind <> "
                "'reply' AND ((m.channel = 'sms' AND NOT c.sms_consent) OR (m.channel = 'email' AND NOT "
                "c.email_on_file))"
            ),
            "over_daily_cap": q(
                "SELECT count(*) FROM (SELECT contact_id, sent_on FROM messages WHERE kind <> 'reply' GROUP BY 1, 2 "
                "HAVING count(*) > 1) x"
            ),
            "holdout_messaged": q(
                "SELECT count(*) FROM messages JOIN outreach_targets USING (target_id) WHERE arm = 'holdout'"
            ),
        }
        info["violations"] = violations
        m["outreach.rule_violations"] = sum(violations.values())
        sent = q("SELECT count(*) FROM messages WHERE kind <> 'reply'")
        m["outreach.opt_out_rate"] = r.opt_outs / sent if sent else 0.0
        m["replies.double_bookings"] = _overlaps(conn, include_plan=False)
        m["replies.refill_winners_per_slot"] = q(
            "SELECT coalesce(max(n), 0) FROM (SELECT slot_key, count(*) n FROM refill_offers WHERE status = 'won' "
            "GROUP BY 1) x"
        )
    info["messages"] = sent
    info["opt_outs"] = r.opt_outs
    info["booked_by_text"] = r.replies.get("booked", 0)
    info["cancellations"] = r.replies.get("cancellations", 0)
    info["refilled"] = r.replies.get("refilled", 0)
    info["questions_to_desk"] = r.replies.get("question", 0)


def _forecast_metrics(engine: Engine, label: str, as_of: date, m: Row) -> None:
    b = backtest(engine, label, as_of)
    b = b[b["actual_hours"] > 0]
    for column in ("hygiene", "doctor"):
        c = b[b["column"] == column]
        if c.empty:
            continue
        m[f"forecast.{column}_weekly_error"] = float(
            np.mean(abs(c["expected_hours"] - c["actual_hours"]) / c["actual_hours"])
        )
        m[f"forecast.{column}_quarter_error"] = abs(float(c["expected_hours"].sum() / c["actual_hours"].sum() - 1))


def run_one(engine: Engine, run: Row, cfg: Row, out_root: Path, seed: int | None = None) -> Row:
    seed = cfg["seed"] if seed is None else seed
    label, office = run["label"], run["office"]
    sim = simulate(office, settings.configs_dir, seed, run.get("scenarios"), run.get("overrides"))
    sim_dir = write(sim, out_root / label / "sim")
    note = ", ".join(run.get("scenarios", [])) or ("stress" if run.get("overrides") else "")
    sync_office(engine, EvalAdapter(label, office, sim_dir, settings.configs_dir, note))
    with office_session(engine, label) as conn:  # a fresh simulation: earlier agent activity no longer applies
        for t in OUTREACH_TABLES:
            conn.execute(text(f"DELETE FROM {t}"))
    with engine.begin() as conn:  # nor do lifts measured on an earlier run; the plan starts from the assumption
        conn.execute(text("DELETE FROM sadhana_meta.lift_estimates WHERE office_id = :o"), {"o": label})
    m: Row = {"data.hidden_truth_columns": _hidden_columns(engine, label)}
    info: Row = {}
    _model_metrics(engine, label, sim, m)
    plan = plan_office(engine, label, SIM_TODAY)
    m["optimizer.plan_vs_first_come"] = plan.expected_value / plan.first_come_value if plan.first_come_value else None
    with office_session(engine, label) as conn:
        m["optimizer.double_bookings"] = _overlaps(conn, include_plan=True)
        done = conn.execute(
            text(
                "SELECT count(*) FILTER (WHERE status IN ('no_show', 'late_cancel')), count(*) FROM appointments "
                "WHERE status IN ('completed', 'no_show', 'late_cancel') AND date > :d"
            ),
            {"d": SIM_TODAY - timedelta(days=365)},
        ).one()
    info["missed_visit_rate"] = round(done[0] / done[1], 4) if done[1] else None
    info["plan_offers"], info["plan_expected_value"] = len(plan.fills), plan.expected_value
    info["at_risk_visits"], info["overbook_slots"] = len(plan.at_risk), len(plan.overbook)
    _outreach_metrics(engine, label, sim_dir, cfg["pilot_days"], m, info)
    _forecast_metrics(engine, label, date.fromisoformat(cfg["backtest_as_of"]), m)
    q = forecast_office(engine, label, SIM_TODAY).summary()
    info["utilization_90d"], info["utilization_90d_with_outreach"] = q["utilization"], q["utilization_with_outreach"]
    info["outreach_value_90d"] = q["outreach_value"]
    checks = grade(m, cfg["checks"])
    return {
        "label": label,
        "office": office,
        "holdout": bool(run.get("holdout")),
        "scenarios": run.get("scenarios", []),
        "overrides": bool(run.get("overrides")),
        "seed": seed,
        "metrics": {
            k: (round(float(v), 4) if isinstance(v, int | float) and v is not None else v) for k, v in m.items()
        },
        "info": info,
        "checks": [asdict(c) for c in checks],
        "passed": all(c.status != "fail" for c in checks),
    }


def render(results: list[Row]) -> str:
    """The scorecard as Markdown: one column per run, one row per check."""
    crashed = [r for r in results if r.get("error")]
    results = [r for r in results if not r.get("error")]
    labels = [r["label"] for r in results]
    names = [c["name"] for c in results[0]["checks"]] if results else []
    mark = {"pass": "ok", "fail": "FAIL", "n/a": "-"}
    lines = ["# Scorecard", ""]
    passed = sum(r["passed"] for r in results)
    lines.append(f"{passed} of {len(results) + len(crashed)} runs pass every check. Seed "
                 f"{results[0]['seed'] if results else ''}.")  # fmt: skip
    lines += [f"- {r['label']} crashed: {r['error']}" for r in crashed]
    covered = [
        r["info"][k] for r in results for k in ("recall_lift_covered", "treatment_lift_covered") if k in r["info"]
    ]
    if covered:
        lines.append(f"Measured outreach lift: {sum(covered)} of {len(covered)} 95% intervals cover the hidden truth "
                     "(about 95% expected).")  # fmt: skip
    lines += [
        "",
        "| Check | Target | " + " | ".join(labels) + " |",
        "| --- | --- | " + " | ".join("---" for _ in labels) + " |",
    ]
    for name in names:
        cells = []
        for r in results:
            c = next(c for c in r["checks"] if c["name"] == name)
            v = "-" if c["value"] is None else f"{c['value']:.3g}"
            cells.append(f"{v} {mark[c['status']]}" if c["status"] != "n/a" else "-")
        op, threshold = next((c["op"], c["threshold"]) for c in results[0]["checks"] if c["name"] == name)
        lines.append(f"| {name} | {op} {threshold} | " + " | ".join(cells) + " |")
    lines += ["", "## What each run looked like", ""]
    keys = INFO_ROWS
    lines += ["| | " + " | ".join(labels) + " |", "| --- | " + " | ".join("---" for _ in labels) + " |"]
    for k in keys:
        vals = []
        for r in results:
            v = r["info"].get(k)
            vals.append("-" if v is None else (f"{v:,.0f}" if isinstance(v, float) and abs(v) >= 100 else f"{v:.3g}"
                                                 if isinstance(v, float) else f"{v:,}"))  # fmt: skip
        lines.append(f"| {k} | " + " | ".join(vals) + " |")
    return "\n".join(lines) + "\n"


def save(engine: Engine, results: list[Row], out_root: Path) -> Path:
    out_root.mkdir(parents=True, exist_ok=True)
    (out_root / "scorecard.json").write_text(json.dumps(results, indent=2, default=str) + "\n")
    path = out_root / "scorecard.md"
    path.write_text(render(results))
    with engine.begin() as conn:
        for r in results:
            conn.execute(
                text(
                    "INSERT INTO sadhana_meta.eval_runs (label, office, seed, passed, results) "
                    "VALUES (:l, :o, :s, :p, CAST(:r AS json))"
                ),
                {"l": r["label"], "o": r["office"], "s": r["seed"], "p": r["passed"], "r": json.dumps(r, default=str)},
            )
    return path
