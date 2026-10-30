"""Fakes and fixtures shared by the YAML generation tests.

No database is needed. FakeDB answers the catalogue queries that db_rules.py sends, from plain dicts, so each test
states the source and the Snowflake copy it wants. The live PostgreSQL test (test_live_postgres.py) is the only
test that connects to anything.
"""

import logging
import re
from types import SimpleNamespace

import pandas as pd

from yaml_generation import generate_yaml
from yaml_generation.settings import TableRule  # noqa: F401  (re-exported for the tests)

LOG = logging.getLogger("yaml_generation.tests")
STAMP = "2026-10-28T10:00:00"

SOURCE_SCHEMA = "edge"
TARGET_DATABASE = "DEV_EDGE_BRONZE"
TARGET_SCHEMA = "EDGE"

# The Fivetran columns, as the shipped settings/exclusions.yaml holds them.
GLOBAL_EXCLUDED = {"_fivetran_synced", "_fivetran_deleted", "_fivetran_id", "_fivetran_index", "_fivetran_active"}

# The source (PostgreSQL) and the Snowflake copy of three tables.
#   units   has an id, an updated_at, and Fivetran columns.
#   notes   has an id and an updated_at.
#   lookup  has no primary key and no id: its data file FAILS (nothing is written for it, its count included).
SOURCE_TABLES = {
    ("edge", "units"): [("id", "integer"), ("unit_name", "character varying"),
                        ("created_at", "timestamp without time zone"), ("updated_at", "timestamp without time zone"),
                        ("_fivetran_synced", "timestamp with time zone"), ("_fivetran_active", "boolean")],
    ("edge", "notes"): [("id", "integer"), ("body", "text"), ("updated_at", "timestamp without time zone"),
                        ("_fivetran_synced", "timestamp with time zone")],
    ("edge", "lookup"): [("code", "character varying"), ("label", "text")],
}
TARGET_TABLES = {
    ("EDGE", "UNITS"): [("ID", "NUMBER"), ("UNIT_NAME", "TEXT"), ("CREATED_AT", "TIMESTAMP_NTZ"),
                        ("UPDATED_AT", "TIMESTAMP_NTZ"), ("_FIVETRAN_SYNCED", "TIMESTAMP_NTZ"), ("_FIVETRAN_ACTIVE", "BOOLEAN")],
    ("EDGE", "NOTES"): [("ID", "NUMBER"), ("BODY", "TEXT"), ("UPDATED_AT", "TIMESTAMP_NTZ"), ("_FIVETRAN_SYNCED", "TIMESTAMP_NTZ")],
    ("EDGE", "LOOKUP"): [("CODE", "TEXT"), ("LABEL", "TEXT")],
}
PRIMARY_KEYS = {("edge", "units"): ["id"], ("edge", "notes"): ["id"]}


class FakeDB:
    """Stands in for a connector. Answers the catalogue queries from dicts.

    tables       {(schema, table): [(column, data_type), ...]}
    primary_keys {(schema, table): [column, ...]}
    fail_on      table names whose column read raises, to test one table failing without stopping the batch.
    queries      every SQL string sent, so a test can check what the generator asked for.
    """

    def __init__(self, tables, primary_keys=None, fail_on=()):
        self.tables = tables
        self.primary_keys = primary_keys or {}
        self.fail_on = set(fail_on)
        self.queries = []

    def execute_query(self, sql):
        self.queries.append(sql)
        schema = _first(sql, r"(?:table_schema|nspname) = '([^']*)'")
        table = _first(sql, r"(?:table_name|relname) = '([^']*)'")
        if "PRIMARY KEY" in sql or "indisprimary" in sql:
            rows = [(column,) for column in self.primary_keys.get((schema, table), [])]
        elif "INFORMATION_SCHEMA.TABLES" in sql or "relkind" in sql:
            rows = [(name,) for s, name in sorted(self.tables) if s == schema]
        else:
            if table in self.fail_on:
                raise RuntimeError(f"catalogue read failed for {table}")
            rows = self.tables.get((schema, table), [])
        return pd.DataFrame(rows)


def _first(sql, pattern):
    found = re.search(pattern, sql)
    return found.group(1) if found else None


def make_args(run_type="historical", tables=("all",), data="yes", count="yes", environment="dev", **overrides):
    """The arguments run_for_validation() builds, through resolve_args(), so defaults match a real run.

    Any attribute can be overridden by keyword, e.g. make_args(source_schema=None) to get the connector's default schema.
    """
    args = SimpleNamespace(source_type="postgres", tables=list(tables), environment=environment,
                           data_validation=data, count_validation=count, source_schema=SOURCE_SCHEMA,
                           target_database=None, target_schema=TARGET_SCHEMA, run_type=run_type)
    for name, value in overrides.items():
        setattr(args, name, value)
    return generate_yaml.resolve_args(args)


def run_generate(args, config_dir, source=None, target=None, global_excluded=GLOBAL_EXCLUDED, table_rules=None,
                 file_filters=None, create_only=False):
    """Call generate() with fake databases. Returns (results, reconciliation_rows)."""
    source = source or FakeDB(SOURCE_TABLES, PRIMARY_KEYS)
    target = target or FakeDB(TARGET_TABLES)
    return generate_yaml.generate(args, source, target, config_dir, global_excluded, table_rules or {}, LOG, STAMP,
                                  file_filters=file_filters, create_only=create_only)


def statuses(results):
    """{(table, kind): status} from the results list."""
    return {(table, kind): status for table, kind, status, _ in results}


def config_dir_for(root, run_type):
    """The folder the generator writes to for a run type, under a temporary root."""
    return root / "config" / run_type / "bronze_postgres"
