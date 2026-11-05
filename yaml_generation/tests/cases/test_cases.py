"""Case tests: does main.py work for each situation, for PostgreSQL (pg_) and SQL Server (ms_)?

Each test asks one question in its docstring. The answers are collected in CASES.md, next to this file.
Databases are fakes (see conftest.py), so no Snowflake or database access is needed.

The exclusions come from two places: database sections in settings/exclusions.yaml (bidirectional, both sides of a
pair) and table rules in the rules workbook (one table, one database and schema).
"""

import re

import pytest


def pg_run(world, *tables, run_type="historical", yaml="no", count="yes", data="yes", filters="no", schema="no"):
    return world.run("--layer_type", "bronze_postgres", "--tables", *tables, "--schema_validation", schema,
                     "--count_validation", count, "--data_validation", data, "--environment", "dev",
                     "--run_type", run_type, "--yaml_generation", yaml, "--filter_files", filters)


def ms_run(world, *tables, run_type="historical", yaml="no", count="yes", data="yes", filters="no", schema="no"):
    return world.run("--layer_type", "bronze_mssql", "--tables", *tables, "--schema_validation", schema,
                     "--count_validation", count, "--data_validation", data, "--environment", "dev",
                     "--run_type", run_type, "--yaml_generation", yaml, "--filter_files", filters)


def status(world, table, kind="data_validation"):
    """The status main.py logged for a table and kind, e.g. NEW, UPDATED, PRESENT, FAILED. None when not logged."""
    found = re.search(rf"YAML (\w+)\s+{re.escape(table)}\s+{kind}", world.log())
    return found.group(1) if found else None


def read(path):
    return path.read_text(encoding="utf-8")


# --- PostgreSQL: row filters ---------------------------------------------------------------------------------------

def test_pg_01_filter_yes_historical_writes_the_filter_into_the_yaml(postgres_filter_files):
    """Q: With --filter_files yes on a historical run, is units' filter written into its YAML?"""
    world = postgres_filter_files
    assert pg_run(world, "units", yaml="yes", filters="yes") == 0
    text = read(world.yaml("historical", "bronze_postgres", "units"))
    assert "(id > 1)" in text
    assert "Filter files: 1 table filter(s) read" in world.log()


def test_pg_02_filter_no_writes_the_whole_table(postgres_filter_files):
    """Q: With --filter_files no, is the filter ignored, so units is compared in full?"""
    world = postgres_filter_files
    assert pg_run(world, "units", yaml="yes", filters="no") == 0
    assert "id > 1" not in read(world.yaml("historical", "bronze_postgres", "units"))
    assert "Filter files:" not in world.log()


def test_pg_03_a_refused_filter_loads_its_table_in_full_with_a_warning(postgres_filter_files):
    """Q: With --filter_files yes, does a filter that the reader refuses ('or') load its table in full, with a warning, and the run goes on?"""
    world = postgres_filter_files
    assert pg_run(world, "notes", yaml="yes", filters="yes") == 0
    assert "body = 'a'" not in read(world.yaml("historical", "bronze_postgres", "notes"))
    assert "loaded in full" in world.log() and "row 3 (notes)" in world.log()


def test_pg_04_incremental_run_has_the_window_and_the_incremental_filter(postgres_filter_files):
    """Q: On an incremental run with --filter_files yes, are the window and units' incremental filter both in the YAML?"""
    world = postgres_filter_files
    assert pg_run(world, "units", run_type="incremental", yaml="yes", filters="yes") == 0
    text = read(world.yaml("incremental", "bronze_postgres", "units"))
    assert '"updated_at" > :start_time AND "updated_at" <= :end_time' in text
    assert "updated_at > '2025-01-01'" in text


# --- PostgreSQL: YAML generation and the missing YAML ------------------------------------------------------------

def test_pg_05_named_tables_are_generated_then_validated(world):
    """Q: With --yaml_generation yes, are both named tables written, and validated, with exit code 0?"""
    assert pg_run(world, "units", "notes", yaml="yes") == 0
    assert status(world, "units") == "NEW" and status(world, "notes") == "NEW"
    assert world.validated[-1] == ("data", ["notes.yaml", "units.yaml"])
    assert ("count", []) in world.validated


def test_pg_06_missing_yaml_is_created_before_validation_when_generation_is_off(world):
    """Q: With --yaml_generation no and no YAML on disk for the named table, does main.py create it and then validate it?"""
    assert not world.exists("historical", "bronze_postgres", "units")
    assert pg_run(world, "units", yaml="no") == 0
    assert world.exists("historical", "bronze_postgres", "units")
    assert status(world, "units") == "NEW"
    assert world.validated[-1] == ("data", ["units.yaml"])


def test_pg_07_existing_yaml_is_used_as_it_is_when_generation_is_off(world):
    """Q: With --yaml_generation no and a YAML already on disk, is the YAML left alone even though the source has changed?"""
    assert pg_run(world, "units", yaml="yes") == 0
    before = read(world.yaml("historical", "bronze_postgres", "units"))
    world.sources["postgres"].tables[("public", "units")].append(("new_col", "text"))
    assert pg_run(world, "units", yaml="no") == 0
    assert read(world.yaml("historical", "bronze_postgres", "units")) == before
    assert world.validated[-1] == ("data", ["units.yaml"])


def test_pg_08_generation_yes_refreshes_a_changed_table(world):
    """Q: With --yaml_generation yes, is a YAML whose source has changed rewritten (UPDATED)?"""
    assert pg_run(world, "units", yaml="yes") == 0
    world.sources["postgres"].tables[("public", "units")].append(("new_col", "text"))
    assert pg_run(world, "units", yaml="yes") == 0
    assert status(world, "units") == "UPDATED"
    assert "new_col" in read(world.yaml("historical", "bronze_postgres", "units"))


def test_pg_09_table_not_in_the_source_fails_and_is_not_validated(world):
    """Q: With --yaml_generation yes and one table that is not in the source, does that table fail, do the others still validate, and does the run exit 1?"""
    assert pg_run(world, "units", "nosuch", yaml="yes") == 1
    assert status(world, "nosuch", kind="table") == "FAILED"
    assert not world.exists("historical", "bronze_postgres", "nosuch")
    assert world.validated[-1] == ("data", ["units.yaml"])


def test_pg_10_missing_table_with_generation_off_is_not_created_and_exit_is_1(world):
    """Q: With --yaml_generation no and a table name that is in neither the YAML folder nor the source, does the run stop with exit 1 and validate nothing?"""
    assert pg_run(world, "nosuch", yaml="no") == 1
    assert "YAML could not be created for nosuch" in world.log()
    assert world.validated == []


def test_pg_11_a_table_without_a_primary_key_fails_and_the_others_run(world):
    """Q: With --yaml_generation yes, does a table with no primary key fail its data (exit 1), with its count still written, while the other named table is still validated?"""
    assert pg_run(world, "units", "lookup", yaml="yes") == 1
    assert status(world, "lookup") == "FAILED"
    assert status(world, "lookup", "count_validation") == "NEW"
    assert world.validated[-1] == ("data", ["units.yaml"])


def test_pg_12_tables_all_takes_the_names_from_the_postgres_workbook(postgres_filter_files):
    """Q: With --tables all, are the tables taken from the postgres filter workbook, and not from every table in the source?"""
    world = postgres_filter_files
    pg_run(world, "all", yaml="yes", count="no", data="no", filters="yes")
    assert world.exists("historical", "bronze_postgres", "units")
    assert world.exists("historical", "bronze_postgres", "notes")
    assert not world.exists("historical", "bronze_postgres", "extra")


def test_pg_13_tables_all_without_workbook_names_is_refused(world):
    """Q: With --tables all and no workbook for postgres, does the run stop with exit 1 and write nothing?"""
    (world.filters / "edge_bronze_filters.xlsx").unlink()
    assert pg_run(world, "all", yaml="yes", count="no", data="no") == 1
    assert "the filter workbook for --layer_type bronze_postgres is missing" in world.log()
    assert not (world.root / "config").exists() or not any((world.root / "config").rglob("*.yaml"))


def test_pg_14_generate_only_writes_yaml_and_runs_no_validator(world):
    """Q: With both validators no and --yaml_generation yes, is the YAML written and no validator run (exit 0)?"""
    assert pg_run(world, "units", yaml="yes", count="no", data="no") == 0
    assert world.exists("historical", "bronze_postgres", "units")
    assert world.validated == []


@pytest.mark.parametrize("schema, count, data, written", [
    ("no", "no", "no", {"schema", "count", "data"}),      # all three no: generate only, every kind
    ("yes", "no", "no", {"schema"}),
    ("no", "yes", "no", {"count"}),
    ("no", "no", "yes", {"data"}),
    ("no", "yes", "yes", {"count", "data"}),
    ("yes", "yes", "yes", {"schema", "count", "data"}),
])
def test_pg_14b_yaml_generation_writes_only_the_kinds_asked_for(world, schema, count, data, written):
    """Q: With --yaml_generation yes, is the YAML written for each kind set to yes, and for all three when all are no?"""
    assert pg_run(world, "units", yaml="yes", schema=schema, count=count, data=data) == 0
    found = {kind for kind, path in (("schema", world.schema_file("historical", "bronze_postgres")),
                                     ("count", world.count_file("historical", "bronze_postgres")),
                                     ("data", world.yaml("historical", "bronze_postgres", "units"))) if path.exists()}
    assert found == written


# --- PostgreSQL: the exclusions and the rules workbook --------------------------------------------------------------

def test_pg_15_a_table_rule_excludes_its_column_for_that_table(world):
    """Q: Does a rules row (postgres, public, units, exclude legacy_code) leave that column out of units' YAML?"""
    world.write_rules([["postgres", "public", "units", None, "legacy_code", None, None, None]])
    assert pg_run(world, "units", yaml="yes") == 0
    text = read(world.yaml("historical", "bronze_postgres", "units"))
    assert '"legacy_code"' not in text and '"LEGACY_CODE"' not in text
    assert "Excluded (table rule): legacy_code" in text


def test_pg_16_a_sql_server_rule_does_not_apply_to_postgres(world):
    """Q: Does a table rule for units in mssql leave a postgres run's units alone (the rule is for its own database)?"""
    world.write_rules([["mssql", "dbo", "units", None, "legacy_code", None, None, None]])
    assert pg_run(world, "units", yaml="yes") == 0
    assert '"legacy_code"' in read(world.yaml("historical", "bronze_postgres", "units"))


def test_pg_17_a_rule_row_without_source_type_stops_the_run(world):
    """Q: Does a rules row with no source_type stop the run with exit 1, and write nothing?"""
    world.write_rules([[None, None, "units", None, "legacy_code", None, None, None]])
    assert pg_run(world, "units", yaml="yes") == 1
    assert "source_type must be one of" in world.log()
    assert not world.exists("historical", "bronze_postgres", "units")


def test_pg_18_a_misspelt_argument_is_refused_before_anything_is_written(world):
    """Q: Is --from_date (removed) refused with exit 2, before any folder or YAML is created?"""
    assert world.run("--layer_type", "bronze_postgres", "--tables", "units", "--schema_validation", "no",
                     "--count_validation", "yes", "--data_validation", "yes", "--environment", "dev",
                     "--from_date", "2026-01-01") == 2
    assert not (world.root / "config").exists()


def test_pg_19_a_postgres_exclusion_leaves_the_column_out_of_both_sides(world):
    """Q: With legacy_code excluded under postgres, is it left out of the postgres side AND the Snowflake side?"""
    world.write_exclusions("postgres:\n  exclude_columns:\n    - legacy_code\n")
    assert pg_run(world, "units", yaml="yes") == 0
    text = read(world.yaml("historical", "bronze_postgres", "units"))
    assert '"legacy_code"' not in text and '"LEGACY_CODE"' not in text
    assert "Excluded (exclusions file): legacy_code" in text


def test_pg_20_a_snowflake_exclusion_applies_to_a_postgres_run_on_both_sides(world):
    """Q: With legacy_code excluded under snowflake, is it left out of a postgres run on both sides?"""
    world.write_exclusions("snowflake:\n  exclude_columns:\n    - legacy_code\n")
    assert pg_run(world, "units", yaml="yes") == 0
    text = read(world.yaml("historical", "bronze_postgres", "units"))
    assert '"legacy_code"' not in text and '"LEGACY_CODE"' not in text


# --- SQL Server: row filters ----------------------------------------------------------------------------------------

def test_ms_01_filter_yes_applies_a_plain_sitelink_filter(sitelink_filter_files):
    """Q: With --filter_files yes, is a SiteLink filter without a bind parameter (Units: UnitID > 100) written into the YAML?"""
    world = sitelink_filter_files
    assert ms_run(world, "Units", yaml="yes", filters="yes") == 0
    assert "(UnitID > 100)" in read(world.yaml("historical", "bronze_mssql", "Units"))


def test_ms_02_a_bind_parameter_filter_loads_its_table_in_full(sitelink_filter_files):
    """Q: With --filter_files yes, does Addresses (filter uses :scorpcode, no value) load in full with a warning, and still generate?"""
    world = sitelink_filter_files
    assert ms_run(world, "Addresses", yaml="yes", filters="yes") == 0
    text = read(world.yaml("historical", "bronze_mssql", "Addresses"))
    assert "scorpcode" not in text
    assert "a bind parameter" in world.log() and "loaded in full" in world.log()


def test_ms_03_filter_no_writes_the_whole_table(sitelink_filter_files):
    """Q: With --filter_files no, is Units' SiteLink filter ignored?"""
    world = sitelink_filter_files
    assert ms_run(world, "Units", yaml="yes", filters="no") == 0
    assert "UnitID > 100" not in read(world.yaml("historical", "bronze_mssql", "Units"))
    assert "Filter files:" not in world.log()


# --- SQL Server: rowversion, exclusions, YAML generation ----------------------------------------------------------

def test_ms_04_rowversion_without_the_sql_server_exclusion_fails_the_table(world):
    """Q: Without the mssql exclusion of uts, does Units (uTS rowversion) fail with exit 1, and write no YAML?"""
    world.write_exclusions("snowflake:\n  exclude_columns:\n    - _fivetran_synced\n")
    assert ms_run(world, "Units", yaml="yes") == 1
    assert status(world, "Units") == "FAILED"
    assert "'uTS' is type timestamp" in world.log()
    assert not world.exists("historical", "bronze_mssql", "Units")


def test_ms_05_the_shipped_mssql_exclusion_leaves_the_rowversion_out(world):
    """Q: With the shipped exclusions file (mssql: uts), is Units written with uTS left out, and exit 0?"""
    assert ms_run(world, "Units", yaml="yes") == 0
    text = read(world.yaml("historical", "bronze_mssql", "Units"))
    assert '"uTS"' not in text and '"UTS"' not in text
    assert "Excluded (exclusions file): uTS" in text


def test_ms_06_a_postgres_exclusion_of_uts_does_not_reach_sql_server(world):
    """Q: Does an exclusion of uts under postgres leave SQL Server's uTS in place, so Units still fails?"""
    world.write_exclusions("postgres:\n  exclude_columns:\n    - uts\n")
    assert ms_run(world, "Units", yaml="yes") == 1
    assert status(world, "Units") == "FAILED"


def test_ms_07_missing_yaml_is_created_when_generation_is_off(world):
    """Q: With --yaml_generation no and no YAML for Units, is it created and then validated?"""
    assert ms_run(world, "Units", yaml="no") == 0
    assert world.exists("historical", "bronze_mssql", "Units")
    assert status(world, "Units") == "NEW"
    assert world.validated[-1] == ("data", ["Units.yaml"])


def test_ms_08_incremental_window_uses_dupdated(world):
    """Q: On an incremental run, is the SQL Server window on dUpdated (not updated_at)?"""
    assert ms_run(world, "Units", run_type="incremental", yaml="yes") == 0
    text = read(world.yaml("incremental", "bronze_mssql", "Units"))
    assert "[dUpdated] > :start_time AND [dUpdated] <= :end_time" in text
    assert "updated_at" not in text


def test_ms_09_an_incremental_table_without_dupdated_is_written_with_the_window_by_name(world):
    """Q: On an incremental run, is Addresses (no dUpdated on either side) written with the window by name on both sides, with a WARNING for each side, and exit 0?"""
    assert ms_run(world, "Addresses", run_type="incremental", yaml="yes") == 0
    assert status(world, "Addresses") == "NEW"
    text = read(world.yaml("incremental", "bronze_mssql", "Addresses"))
    assert "[dupdated] > :start_time" in text
    assert '"DUPDATED" > :start_time' in text
    assert "incremental column dupdated is not compared on the SQL Server side" in world.log()
    assert "incremental column dupdated is not compared on the Snowflake side" in world.log()


def test_ms_10_a_table_without_a_primary_key_fails_its_data_and_still_counts(world):
    """Q: Does Lookup (no primary key) fail its data with exit 1, with its count still written?"""
    assert ms_run(world, "Lookup", yaml="yes") == 1
    assert "unable to find the primary key" in world.log()
    assert status(world, "Lookup", "count_validation") == "NEW"


def test_ms_11_tables_all_takes_the_names_from_the_sitelink_workbook(sitelink_filter_files):
    """Q: With --tables all, are the tables Units, Addresses and Lookup (the SiteLink workbook), and not Charges (in the source only)?"""
    world = sitelink_filter_files
    assert ms_run(world, "all", yaml="yes", count="no", data="no") == 1
    assert world.exists("historical", "bronze_mssql", "Units")
    assert world.exists("historical", "bronze_mssql", "Addresses")
    assert not world.exists("historical", "bronze_mssql", "Charges")
    assert status(world, "Lookup") == "FAILED"


def test_ms_12_a_wrong_target_schema_writes_nothing(world):
    """Q: With a target_schema in the workbook that does not exist in Snowflake, does Units fail with exit 1 and no YAML?"""
    from conftest import SITELINK, sitelink_filters
    world.write_filters("sitelink_bronze_filters.xlsx", sitelink_filters(SITELINK[:5] + ["NOPE"]))
    assert ms_run(world, "Units", yaml="yes") == 1
    assert "no tables visible in Snowflake schema" in world.log()
    assert not world.exists("historical", "bronze_mssql", "Units")


def test_ms_13_a_snowflake_exclusion_applies_to_a_sql_server_run_on_both_sides(world):
    """Q: With dcwidth excluded under snowflake, is it left out of a SQL Server run on both sides?"""
    world.write_exclusions("snowflake:\n  exclude_columns:\n    - dcwidth\n    - _fivetran_synced\n")
    assert ms_run(world, "Units", yaml="yes") == 1, "uTS still needs its exclusion, so Units still fails"
    world.write_exclusions("snowflake:\n  exclude_columns:\n    - dcwidth\n    - _fivetran_synced\nmssql:\n  exclude_columns:\n"
                           "    - uts\n")
    assert ms_run(world, "Units", yaml="yes") == 0
    text = read(world.yaml("historical", "bronze_mssql", "Units"))
    assert "[dcWidth]" not in text and '"DCWIDTH"' not in text
    assert re.search(r"Excluded \(exclusions file\):.*dcWidth", text)


# --- the defaults and the arguments: the workbook replaces --source_schema, --target_database, --target_schema ---------

DEFAULT_RUN = ("--layer_type", "bronze_postgres", "--tables", "units", "--schema_validation", "no", "--count_validation", "yes",
               "--data_validation", "yes", "--environment", "dev")


def test_pg_21_without_the_flags_the_yaml_is_generated_and_the_filter_applied(world):
    """Q: With neither --yaml_generation nor --filter_files given, are both yes on a bronze run (YAML written, filter in it)?"""
    assert world.run(*DEFAULT_RUN) == 0
    assert status(world, "units") == "NEW"
    assert "(id > 1)" in read(world.yaml("historical", "bronze_postgres", "units"))


def test_pg_22_an_unchanged_table_is_not_rewritten_by_the_default_run(world):
    """Q: On a second default run with nothing changed, is the YAML left alone (UNCHANGED, same text)?"""
    assert world.run(*DEFAULT_RUN) == 0
    before = read(world.yaml("historical", "bronze_postgres", "units"))
    assert world.run(*DEFAULT_RUN) == 0
    assert status(world, "units") == "UNCHANGED"
    assert read(world.yaml("historical", "bronze_postgres", "units")) == before


def test_pg_23_the_location_arguments_are_gone(world):
    """Q: Is --source_schema (now read from the workbook) refused as an unknown argument, before anything is written?"""
    assert world.run(*DEFAULT_RUN, "--source_schema", "public") == 2
    assert not (world.root / "config").exists()


def test_pg_24_a_table_named_but_not_in_the_workbook_fails_and_the_others_validate(world):
    """Q: Is a source table with no workbook row (extra) FAILED with the reason, while units is still validated?"""
    assert pg_run(world, "units", "extra", yaml="yes") == 1
    assert status(world, "extra", kind="table") == "FAILED"
    assert "no row in the filter workbooks for PostgreSQL" in world.log()
    assert world.validated[-1] == ("data", ["units.yaml"])


def test_ms_14_the_sql_server_location_comes_from_its_workbook(world):
    """Q: Does a SQL Server run read schema dbo and Snowflake {env}_SITELINK_BRONZE.AWSDEV_DEVT5000_DBO from the SiteLink rows?"""
    assert ms_run(world, "Units", yaml="yes") == 0
    text = read(world.yaml("historical", "bronze_mssql", "Units"))
    assert "source_schema: dbo" in text and "{env}_SITELINK_BRONZE" in text and "AWSDEV_DEVT5000_DBO" in text


def test_other_layers_keep_both_flags_off_by_default(monkeypatch):
    """Q: Does a silver run, which has no generator, still default --yaml_generation and --filter_files to no?"""
    import sys

    from utils.utility import parse_arguments
    monkeypatch.setattr(sys, "argv", ["main.py", "--layer_type", "silver", "--tables", "all", "--schema_validation", "no",
                                      "--count_validation", "yes", "--data_validation", "yes", "--environment", "dev"])
    assert [parse_arguments().__dict__[flag] for flag in ("yaml_generation", "filter_files")] == [["no"], ["no"]]
