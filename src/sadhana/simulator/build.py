"""Build one office's full synthetic practice: patients, charts and three years of history."""

from __future__ import annotations

import csv
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sadhana.simulator import charts, history, money
from sadhana.simulator.population import Patient, Truth, generate
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
    money: money.Money | None = None


def simulate(office_id: str, configs_dir: Path, seed: int = 42) -> Sim:
    office = json.loads((configs_dir / "offices" / f"{office_id}.json").read_text())
    with (configs_dir / "payers" / f"payers_{office['state']}.csv").open(newline="") as f:
        payers = {r["payer"]: r for r in csv.DictReader(f)}
    patients, truths = generate(office, configs_dir, payers, seed=seed)
    former, former_truths = history.former_patients(office, configs_dir, payers, patients, seed)
    everyone, all_truths = patients + former, truths + former_truths
    teeth, conds, perio, plan = charts.generate(everyone, all_truths, seed=seed)
    former_ids = {p.patient_id for p in former}
    n_chart = len(conds)
    appts, appt_truth, procs, hist, flows = history.run(
        office, everyone, all_truths, teeth, conds, perio, plan, configs_dir, former_ids, seed=seed
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
    sim.money = money.run(sim, configs_dir, seed=seed)
    return sim


def write(sim: Sim, out_dir: Path) -> Path:
    write_population(sim.patients, sim.truths, out_dir)
    charts.write(sim.teeth, sim.conditions, sim.perio, sim.plan, out_dir)
    history.write(sim.appointments, sim.appointment_truth, sim.procedures, sim.history, sim.flows, out_dir)
    if sim.money:
        money.write(sim.money, out_dir)
    return out_dir
