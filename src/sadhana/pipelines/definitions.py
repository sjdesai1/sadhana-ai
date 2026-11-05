"""Dagster definitions. Nightly work is modeled as data assets that depend on each other.

Today: economics tables for every office. Coming: sync, features, scores, benefits,
the 90-day plan, and reports, each as its own asset downstream of the last.
"""

from dagster import (
    AssetExecutionContext,
    Definitions,
    ScheduleDefinition,
    asset,
    define_asset_job,
)

from sadhana.config import settings
from sadhana.economics.engine import build, load_inputs, write_outputs


def office_ids() -> list[str]:
    return sorted(p.stem for p in (settings.configs_dir / "offices").glob("*.json"))


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


nightly_job = define_asset_job("nightly", selection=[economics_tables])

defs = Definitions(
    assets=[economics_tables],
    jobs=[nightly_job],
    schedules=[ScheduleDefinition(job=nightly_job, cron_schedule="0 2 * * *", execution_timezone="America/New_York")],
)
