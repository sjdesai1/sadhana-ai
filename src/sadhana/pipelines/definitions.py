"""Dagster definitions. Nightly work is modeled as data assets that depend on each other.

Today: sync every demo office into Postgres, then train and score the models on it; the economics tables are
built from config, so they do not wait on the sync. The slot plan follows the scores, outreach follows
the plan, and the 90-day plan follows outreach. Coming: reports.
"""

from pathlib import Path

from dagster import (
    AssetExecutionContext,
    Definitions,
    ScheduleDefinition,
    asset,
    define_asset_job,
)

from sadhana.agents.run import run_outreach
from sadhana.config import settings
from sadhana.data.db import get_engine
from sadhana.economics.engine import build, load_inputs, write_outputs
from sadhana.models.run import run_office
from sadhana.optimizer.horizon import forecast_office
from sadhana.optimizer.run import plan_office
from sadhana.simulator.population import SIM_TODAY
from sadhana.sync.adapters import SimulatorAdapter
from sadhana.sync.run import sync_office


def office_ids() -> list[str]:
    return sorted(p.stem for p in (settings.configs_dir / "offices").glob("*.json"))


@asset(group_name="sync")
def office_sync(context: AssetExecutionContext) -> None:
    """Every demo office's data, refreshed from its source into its own Postgres schema."""
    engine = get_engine()
    total = 0
    for office_id in office_ids():
        adapter = SimulatorAdapter(office_id, Path(settings.outputs_dir) / office_id / "sim", settings.configs_dir)
        counts = sync_office(engine, adapter)
        total += sum(counts.values())
        context.log.info(f"{office_id}: {sum(counts.values())} rows")
    context.add_output_metadata({"offices": len(office_ids()), "rows": total})


@asset(group_name="models", deps=[office_sync])
def model_scores(context: AssetExecutionContext) -> None:
    """Missed-visit and recall scores for every synced demo office, tested on the latest months first."""
    engine = get_engine()
    for office_id in office_ids():
        for r in run_office(engine, office_id, SIM_TODAY):
            context.log.info(f"{office_id} {r.model}: AUC {r.auc:.3f}, scored {len(r.scores)}")


@asset(group_name="optimizer", deps=[model_scores])
def slot_plan(context: AssetExecutionContext) -> None:
    """Who to offer each open slot in the next two weeks, and which booked visits are at risk."""
    engine = get_engine()
    for office_id in office_ids():
        s = plan_office(engine, office_id, SIM_TODAY).summary()
        context.log.info(f"{office_id}: {s['filled_blocks']} offers, expected ${s['expected_value']:,.0f}")


@asset(group_name="agents", deps=[slot_plan])
def outreach(context: AssetExecutionContext) -> None:
    """Tonight's outreach for every demo office: confirmations, slot offers, treatment follow-up and recall, with
    the holdout group left alone; simulated patients answer, then the lift is measured."""
    engine = get_engine()
    for office_id in office_ids():
        r = run_outreach(engine, office_id, SIM_TODAY, days=1, sim_dir=Path(settings.outputs_dir) / office_id / "sim")
        context.log.info(f"{office_id}: {r.by_channel} messages, {r.opt_outs} opt-outs")


@asset(group_name="optimizer", deps=[outreach])
def plan_90(context: AssetExecutionContext) -> None:
    """The next 13 weeks for every demo office, using tonight's measured outreach lift."""
    engine = get_engine()
    for office_id in office_ids():
        s = forecast_office(engine, office_id, SIM_TODAY).summary()
        context.log.info(f"{office_id}: fills {s['utilization']:.0%}, outreach to {s['utilization_with_outreach']:.0%}")


@asset(group_name="economics")
def economics_tables(context: AssetExecutionContext) -> None:
    """Procedure x payer x provider margins and best provider per procedure, per office."""
    counts: dict[str, int] = {}
    for office_id in office_ids():
        inputs = load_inputs(settings.configs_dir / "offices" / f"{office_id}.json", settings.configs_dir)
        result = build(inputs)
        write_outputs(result, settings.outputs_dir)
        counts[office_id] = len(result.detail)
        context.log.info(f"{office_id}: {len(result.detail)} rows")
    context.add_output_metadata({"offices": len(counts), "rows": sum(counts.values())})


nightly_job = define_asset_job(
    "nightly", selection=[office_sync, model_scores, slot_plan, outreach, plan_90, economics_tables]
)

defs = Definitions(
    assets=[office_sync, model_scores, slot_plan, outreach, plan_90, economics_tables],
    jobs=[nightly_job],
    schedules=[ScheduleDefinition(job=nightly_job, cron_schedule="0 2 * * *", execution_timezone="America/New_York")],
)
