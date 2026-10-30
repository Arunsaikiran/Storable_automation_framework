"""Live PostgreSQL check of the generator's catalogue reads, against the dev database in creds/dev.yaml.

Opt-in and read-only. The tests run only when creds/dev.yaml exists AND the environment variable RUN_LIVE_POSTGRES=1
is set, so the default test run never touches the network. Only SELECT statements are sent (the catalogue reads in
db_rules.py). The YAML is written to a temporary folder. Snowflake is still a fake (helpers.FakeDB). Run with:

    set RUN_LIVE_POSTGRES=1
    python -m pytest yaml_generation/tests/test_live_postgres.py
"""

import os
from pathlib import Path

import pytest

from helpers import FakeDB, config_dir_for, make_args, run_generate, statuses
from yaml_generation.db_rules import RULES, list_columns, list_tables, primary_key_columns

FRAMEWORK_ROOT = Path(__file__).resolve().parents[2]
CREDS = FRAMEWORK_ROOT / "creds" / "dev.yaml"
LIVE = CREDS.exists() and os.environ.get("RUN_LIVE_POSTGRES") == "1"
pytestmark = pytest.mark.skipif(not LIVE, reason="needs creds/dev.yaml and RUN_LIVE_POSTGRES=1")


def _source():
    from db.factory import get_database
    return get_database("postgres", str(FRAMEWORK_ROOT), "dev")


def _first_table(source):
    tables = list_tables(source, "public")
    if not tables:
        pytest.skip("no base tables in the public schema of the dev database")
    return tables[0]


def test_live_catalogue_reads_return_names_types_and_the_key():
    source = _source()
    table = _first_table(source)
    columns = list_columns(source, "public", table)
    assert columns and all(isinstance(name, str) and isinstance(dtype, str) for name, dtype in columns)
    keys = primary_key_columns(source, "public", table, RULES["postgres"])
    assert isinstance(keys, list) and set(keys) <= {name for name, _ in columns}
    assert list_columns(source, "public", "no_such_table_here") == []


def test_live_columns_drive_the_historical_yaml(tmp_path):
    """The generator, given the live columns, writes the count YAML and reports no FAILED table.

    Snowflake is the fake: it holds the same column names in upper case, as Snowflake does.
    """
    source = _source()
    table = _first_table(source)
    columns = list_columns(source, "public", table)
    target = FakeDB({("EDGE", table.upper()): [(name.upper(), "TEXT") for name, _ in columns]})
    args = make_args("historical", tables=(table,), source_schema="public")
    config = config_dir_for(tmp_path, "historical")
    results, _ = run_generate(args, config, source=source, target=target)
    assert "FAILED" not in {status for _, _, status, _ in results}
    assert statuses(results)[(table, "count_validation")] == "NEW"
    assert (config / "count_validation" / "bronze_postgres.yaml").exists()
