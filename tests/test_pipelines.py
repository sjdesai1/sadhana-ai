from dagster import materialize

from sadhana.pipelines.definitions import defs, economics_tables


def test_definitions_load() -> None:
    assert defs.get_job_def("nightly") is not None


def test_economics_asset_materializes() -> None:
    assert materialize([economics_tables]).success
