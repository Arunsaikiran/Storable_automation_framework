"""The harness for the case tests: main.py runs in-process, end to end, with nothing outside a temporary folder touched.

  - Databases: fakes (helpers.FakeDB). PostgreSQL and SQL Server each have a source; Snowflake is one target holding both
    schemas. No connection is opened, so the cases run without database or Snowflake access.
  - Folders: config/, output/ and the master (filter) workbooks live under a temporary root.
  - Exclusions: only the master rows' source/target_exclude_columns cells, changed per case with World.set_cell().
  - Validators: stubs that record the data YAML they are given, so a case can show what main.py would validate.
  - Exit code: main.main() ends in sys.exit(code). The harness returns that code.
"""

import copy
import logging
import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from openpyxl import Workbook

import main
from helpers import FakeDB
from yaml_generation import filter_files, generate_yaml

REPO = os.path.dirname(os.path.abspath(main.__file__))


# --- the databases -----------------------------------------------------------------------------------------------

def postgres_source():
    return FakeDB({
        ("public", "units"): [("id", "integer"), ("unit_name", "character varying"), ("legacy_code", "character varying"),
                              ("created_at", "timestamp without time zone"), ("updated_at", "timestamp without time zone"),
                              ("_fivetran_synced", "timestamp with time zone")],
        ("public", "notes"): [("id", "integer"), ("body", "text"), ("updated_at", "timestamp without time zone"),
                              ("_fivetran_synced", "timestamp with time zone")],
        ("public", "lookup"): [("code", "character varying"), ("label", "text")],
        ("public", "extra"): [("id", "integer")],
    }, {("public", "units"): ["id"], ("public", "notes"): ["id"], ("public", "extra"): ["id"]})


def sqlserver_source():
    return FakeDB({
        ("dbo", "Units"): [("UnitID", "int"), ("UnitName", "varchar"), ("dUpdated", "datetime"), ("uTS", "timestamp"),
                           ("dcWidth", "money")],
        ("dbo", "Addresses"): [("AddrID", "int"), ("Street", "varchar")],
        ("dbo", "Lookup"): [("Code", "varchar"), ("Label", "varchar")],
        ("dbo", "Charges"): [("ChargeID", "int"), ("dUpdated", "datetime")],
    }, {("dbo", "Units"): ["UnitID"], ("dbo", "Addresses"): ["AddrID"], ("dbo", "Charges"): ["ChargeID"]})


def snowflake_target():
    """Both target schemas: the PostgreSQL one (STOREDGE_FMS_PUBLIC) and the SQL Server site one (AWSDEV_DEVT5000_DBO)."""
    return FakeDB({
        ("STOREDGE_FMS_PUBLIC", "UNITS"): [("ID", "NUMBER"), ("UNIT_NAME", "TEXT"), ("LEGACY_CODE", "TEXT"),
                                           ("CREATED_AT", "TIMESTAMP_NTZ"), ("UPDATED_AT", "TIMESTAMP_NTZ"),
                                           ("_FIVETRAN_SYNCED", "TIMESTAMP_NTZ"), ("_FIVETRAN_ACTIVE", "BOOLEAN")],
        ("STOREDGE_FMS_PUBLIC", "NOTES"): [("ID", "NUMBER"), ("BODY", "TEXT"), ("UPDATED_AT", "TIMESTAMP_NTZ"),
                                           ("_FIVETRAN_SYNCED", "TIMESTAMP_NTZ"), ("_FIVETRAN_ACTIVE", "BOOLEAN")],
        ("STOREDGE_FMS_PUBLIC", "LOOKUP"): [("CODE", "TEXT"), ("LABEL", "TEXT")],
        ("AWSDEV_DEVT5000_DBO", "UNITS"): [("UNITID", "NUMBER"), ("UNITNAME", "TEXT"), ("DUPDATED", "TIMESTAMP_NTZ"),
                                           ("UTS", "BINARY"), ("DCWIDTH", "NUMBER"), ("_FIVETRAN_ACTIVE", "BOOLEAN")],
        ("AWSDEV_DEVT5000_DBO", "ADDRESSES"): [("ADDRID", "NUMBER"), ("STREET", "TEXT")],
        ("AWSDEV_DEVT5000_DBO", "LOOKUP"): [("CODE", "TEXT"), ("LABEL", "TEXT")],
    })


# --- the workbooks -----------------------------------------------------------------------------------------------

def write_workbook(path, sheets):
    """sheets = {sheet_name: [header_row, data_row, ...]}"""
    book = Workbook()
    book.remove(book.active)
    for name, rows in sheets.items():
        ws = book.create_sheet(name)
        for row in rows:
            ws.append(row)
    book.save(path)
    return path


LOCATION_HEADERS = ["source_application", "source_database", "source_schema", "target_application", "target_database",
                    "target_schema"]
EDGE = ["Edge", "fms", "public", "Snowflake", "{env}_EDGE_BRONZE", "STOREDGE_FMS_PUBLIC"]
SITELINK = ["SiteLink", "DevT5000", "dbo", "Snowflake", "{env}_SITELINK_BRONZE", "AWSDEV_DEVT5000_DBO"]

# The type mapping sheets (filter_files.SCHEMA_SHEETS) for the fake sources' types, read by --schema_validation yes.
MAPPING_HEADER = ["source_data_type", "target_data_type", "precision_check", "notes"]
POSTGRES_MAPPING = [MAPPING_HEADER, ["integer", "number", "numeric"], ["character varying", "text", "character"],
                    ["text", "text"], ["timestamp without time zone", "timestamp_ntz", "datetime"],
                    ["timestamp with time zone", "timestamp_tz"]]
MSSQL_MAPPING = [MAPPING_HEADER, ["int", "number", "numeric"], ["varchar", "text", "character"],
                 ["datetime", "timestamp_ntz", "datetime"], ["timestamp", "binary"], ["money", "number", "numeric"]]

# The Fivetran columns of the fake Snowflake copies, as a target_exclude_columns cell names them.
FIVETRAN = "_fivetran_synced,_fivetran_active"
EXCLUDE_AND_ACTIVE = ["source_exclude_columns", "target_exclude_columns", "active"]
SITELINK_EXCLUDE = ["uts", "uts," + FIVETRAN, "yes"]   # uts, the rowversion, on both sides as the shipped rows do

# The filter workbooks are where each table lives. 'extra' (PostgreSQL) and 'Charges' (SQL Server) are in the source only.
POSTGRES_FILTERS = {"filters": [
    # One filter, incremental column, key and exclude cell per side, and active (25/11/2026).
    ["Target Table Name", "Incremental Source Filter", "Incremental Target Filter", "Historical Source Filter",
     "Historical Target Filter", *LOCATION_HEADERS, "source_incremental_column", "target_incremental_column",
     "source_key_columns", *EXCLUDE_AND_ACTIVE],
    ["units", "units\nwhere updated_at > '2025-01-01'", "units\nwhere updated_at > '2025-01-01'", "units\nwhere id > 1",
     "units\nwhere id > 1", *EDGE, "updated_at", "updated_at", None, None, FIVETRAN, "yes"],
    ["notes", None, None, "notes n\nwhere n.body = 'a' or n.body = 'b'", "notes n\nwhere n.body = 'a' or n.body = 'b'",
     *EDGE, "updated_at", "updated_at", None, None, FIVETRAN, "yes"],         # refused: 'or'
    ["lookup", None, None, None, None, *EDGE, "updated_at", "updated_at", None, None, FIVETRAN, "yes"],
], "Postgres schema mapping": POSTGRES_MAPPING}


def sitelink_filters(location=SITELINK):
    return {"Sheet1": [
        ["s.no", "Target Table Name", "Historical Source Filter", "Historical Target Filter", "Incremental Source Filter",
         "Incremental Target Filter", *LOCATION_HEADERS, "source_incremental_column", "target_incremental_column",
         *EXCLUDE_AND_ACTIVE],
        [1, "Units", *["Units t\nwhere t.UnitID > 100"] * 4, *location, "dupdated", "dupdated", *SITELINK_EXCLUDE],
        [2, "Addresses", *["Addresses t\nwhere t.scorpcode = :scorpcode"] * 4, *location, "dupdated", "dupdated",
         *SITELINK_EXCLUDE],  # bind
        [3, "Lookup", *["compare full"] * 4, *location, "dupdated", "dupdated", *SITELINK_EXCLUDE],
    ], "MSSQL schema mapping": MSSQL_MAPPING}


SITELINK_FILTERS = sitelink_filters()


# --- the run -----------------------------------------------------------------------------------------------------

class World:
    """One temporary world for a case: folders, workbooks, databases, and what main.py did in it."""

    def __init__(self, root, monkeypatch):
        self.root = root
        self.monkeypatch = monkeypatch
        self.validated = []          # [(kind, [data YAML file names given to the validator])]
        self.filters = root / "filters"
        self.filters.mkdir()
        self.sheets = {}             # {workbook name: sheets as last written}

    # the workbooks
    def write_filters(self, name, sheets):
        self.sheets[name] = copy.deepcopy(sheets)
        write_workbook(self.filters / name, self.sheets[name])

    def set_cell(self, name, table, header, value):
        """Sets one cell of a table's row in workbook name (its first sheet) and writes the workbook again."""
        rows = next(iter(self.sheets[name].values()))
        at, table_at = rows[0].index(header), rows[0].index("Target Table Name")
        for row in rows[1:]:
            if row[table_at] == table:
                row[at] = value
        write_workbook(self.filters / name, self.sheets[name])

    # the run
    def run(self, *args):
        self.monkeypatch.setattr(sys, "argv", ["main.py", *args])
        try:
            main.main()
        except SystemExit as stop:
            return stop.code
        raise AssertionError("main.main() did not end with sys.exit")

    # what was written
    def yaml(self, run_type, layer, table):
        return self.root / "config" / run_type / layer / "data_validation" / f"{table}.yaml"

    def count_file(self, run_type, layer):
        return self.root / "config" / run_type / layer / "count_validation" / f"{layer}.yaml"

    def log(self):
        logs = sorted((self.root / "output").rglob("validation_*.log"), key=lambda p: p.stat().st_mtime)
        return logs[-1].read_text(encoding="utf-8") if logs else ""

    def schema_file(self, run_type, layer):
        return self.root / "config" / run_type / layer / "schema_validation" / "schema_validation.yaml"

    def exists(self, run_type, layer, table):
        return self.yaml(run_type, layer, table).exists()


@pytest.fixture
def world(monkeypatch, tmp_path):
    root = tmp_path / "framework"
    root.mkdir()
    w = World(root, monkeypatch)

    # Paths: everything main.py and the generator write goes under the temporary root.
    monkeypatch.setattr(generate_yaml, "FRAMEWORK_ROOT", root)
    monkeypatch.setattr(filter_files, "FOLDER", w.filters)
    # Every run reads where its tables live from the filter workbooks, so both applications' workbooks are there by default.
    w.write_filters("bronze_postgres_edge_master.xlsx", POSTGRES_FILTERS)
    w.write_filters("bronze_mssql_sitelink_master.xlsx", SITELINK_FILTERS)

    real_paths = main.get_config_output_paths

    def under_root(run_id, layer, report_pack, base_dir, config_path, validation_dirs, table_list):
        return real_paths(run_id, layer, report_pack, str(root), config_path.replace(REPO, str(root)), validation_dirs,
                          table_list)
    monkeypatch.setattr(main, "get_config_output_paths", under_root)

    # Databases: one source per database type, one target for Snowflake. The sources are kept on the world, so a case can
    # change a source table between two runs.
    w.sources = {"postgres": postgres_source(), "mssql": sqlserver_source()}
    target = snowflake_target()
    monkeypatch.setattr(generate_yaml, "get_database",
                        lambda db_type, base, env: target if db_type == "snowflake" else w.sources[db_type])

    # Validators: stubs that record which data YAML main.py gave them. No database is read.
    def validate_schema(ctx):
        return 0, False

    def validate_count(ctx):
        w.validated.append(("count", []))
        return 0, False

    def validate_data(ctx):
        w.validated.append(("data", sorted(Path(p).name for p in ctx.configpaths.get("data_validation", []))))
        return 0, False

    monkeypatch.setattr(main, "validate_schema", validate_schema)
    monkeypatch.setattr(main, "validate_count", validate_count)
    monkeypatch.setattr(main, "validate_data", validate_data)

    yield w

    # main.py adds a log file handler per run. Remove them, so one case does not write to another case's folder.
    logger = logging.getLogger("main")
    for handler in list(logger.handlers):
        if isinstance(handler, logging.FileHandler):
            logger.removeHandler(handler)
            handler.close()


@pytest.fixture
def postgres_filter_files(world):
    """The world, named for the cases about the Edge workbook's filters (the workbook is always there)."""
    return world


@pytest.fixture
def sitelink_filter_files(world):
    """The world, named for the cases about the SiteLink workbook's filters (the workbook is always there)."""
    return world
