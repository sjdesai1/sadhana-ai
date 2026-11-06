"""Build one office's full synthetic practice: patients, charts and three years of history."""

from __future__ import annotations

import csv
import json
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

from sadhana.simulator import charts, events, history, money
from sadhana.simulator.population import SIM_TODAY, Patient, Truth, generate
from sadhana.simulator.population import write as write_population
from sadhana.simulator.schedule import Appointment, AppointmentTruth, Procedure


@dataclass
class Sim:
    office: dict[str, Any]
    patients: list[Patient]  # current and former
    truths: list[Truth]
    current_ids: set[str]
    former_ids: set[str]
    teeth: list[charts.Tooth]
    conditions: list[charts.Condition]
    chart_conditions: int  # conditions[:chart_conditions] came from the chart; the rest were found later
    perio: list[charts.Perio]
    plan: list[charts.PlanItem]
    appointments: list[Appointment]
    appointment_truth: list[AppointmentTruth]
    procedures: list[Procedure]
    history: list[history.PatientHistory]
    flows: list[history.FlowTruth]
    coverage: events.Coverage | None = None
    life_events: list[events.LifeEvent] | None = None
    money: money.Money | None = None


def load_office(office_id: str, configs_dir: Path) -> dict[str, Any]:
    """Demo offices live in configs/offices; hidden test offices in configs/offices_holdout."""
    for folder in ("offices", "offices_holdout"):
        path = configs_dir / folder / f"{office_id}.json"
        if path.exists():
            return json.loads(path.read_text())
    raise FileNotFoundError(office_id)


def simulate(
    office_id: str,
    configs_dir: Path,
    seed: int = 42,
    scenarios: list[str] | None = None,
    overrides: dict[str, Any] | None = None,
) -> Sim:
    """Build one office. `scenarios` (recession, payer_cut, competitor) and `overrides` (stress settings, any
    clock.json value) are added to what the office config already asks for."""
    office = load_office(office_id, configs_dir)
    sim_cfg = dict(office.get("simulation", {}))
    if scenarios:
        sim_cfg["scenarios"] = [*sim_cfg.get("scenarios", []), *scenarios]
    if overrides:
        sim_cfg["overrides"] = events.deep_merge(sim_cfg.get("overrides", {}), overrides)
    office = {**office, "simulation": sim_cfg}
    with (configs_dir / "payers" / f"payers_{office['state']}.csv").open(newline="") as f:
        payers = {r["payer"]: r for r in csv.DictReader(f)}
    patients, truths = generate(office, configs_dir, payers, seed=seed)
    former, former_truths = history.former_patients(office, configs_dir, payers, patients, seed)
    everyone, all_truths = patients + former, truths + former_truths
    teeth, conds, perio, plan = charts.generate(everyone, all_truths, seed=seed)
    former_ids = {p.patient_id for p in former}
    n_chart = len(conds)
    years = events.load_clock(configs_dir, office)["history_years"]
    start = date(SIM_TODAY.year - years, SIM_TODAY.month, 1)
    coverage, life = events.generate(office, everyone, configs_dir, start, seed=seed)
    appts, appt_truth, procs, hist, flows = history.run(
        office, everyone, all_truths, teeth, conds, perio, plan, configs_dir, former_ids, seed=seed, coverage=coverage
    )
    current = {p.patient_id for p in patients}
    sim = Sim(
        office,
        everyone,
        all_truths,
        current,
        former_ids,
        teeth,
        conds,
        n_chart,
        perio,
        plan,
        appts,
        appt_truth,
        procs,
        hist,
        flows,
    )
    sim.coverage, sim.life_events = coverage, life
    sim.money = money.run(sim, configs_dir, seed=seed)
    return sim


def write(sim: Sim, out_dir: Path) -> Path:
    write_population(sim.patients, sim.truths, out_dir)
    charts.write(sim.teeth, sim.conditions, sim.perio, sim.plan, out_dir)
    history.write(sim.appointments, sim.appointment_truth, sim.procedures, sim.history, sim.flows, out_dir)
    if sim.money:
        money.write(sim.money, out_dir)
    if sim.life_events is not None:
        events.write(sim.life_events, out_dir)
    return out_dir
