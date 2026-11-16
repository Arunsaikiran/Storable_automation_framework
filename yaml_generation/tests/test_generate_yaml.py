"""Integration tests for generate_yaml.py: the whole generation for one run, against fake source and Snowflake databases.

Each test writes into a temporary folder, so the real config/ and output/ folders are never touched. The fakes are
described in helpers.py. The live PostgreSQL run is in test_live_postgres.py.
"""

import logging
from types import SimpleNamespace

import pytest
import yaml
from openpyxl import Workbook

from helpers import (LOG, PRIMARY_KEYS, SOURCE_SCHEMA, SOURCE_TABLES, TARGET_SCHEMA, TARGET_TABLES, FakeDB, TableRule,
                     config_dir_for, make_args, run_generate, statuses)
from yaml_generation import filter_files, generate_yaml, settings
from yaml_generation import yaml_builder as yb


def read_yaml(path):
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def data_block(config, table):
    return read_yaml(config / "data_validation" / f"{table}.yaml")["tables"][table]["validations"]["data_validation"]


def count_block(config, layer_file, table):
    return read_yaml(config / "count_validation" / f"{layer_file}.yaml")["tables"][table]["validations"]["count_validation"]


def detail_of(results, table, kind):
    return next(detail for t, k, _, detail in results if t == table and k == kind)


LOCATION = ["Edge", "fms", SOURCE_SCHEMA, "Snowflake", "{env}_EDGE_BRONZE", TARGET_SCHEMA]
WORKBOOK_HEADER = ["Target Table Name", "Historical Filter to apply.", "Incremental filters to apply",
                   *filter_files.LOCATION_HEADERS]


# The type mapping of the fake source, as the 'Postgres schema mapping' sheet lists it.
TYPE_MAPPING = [["integer", "number", "numeric"], ["character varying", "text", "character"], ["text", "text"],
                ["timestamp without time zone", "timestamp_ntz", "datetime"], ["timestamp with time zone", "timestamp_tz"],
                ["boolean", "boolean"]]


def write_filter_workbook(path, rows, mapping=TYPE_MAPPING):
    """rows: [table, historical filter, incremental filter] (+ location, default LOCATION). mapping: the type mapping sheet
    rows (None: no sheet). Returns the path."""
    book = Workbook()
    ws = book.active
    ws.title = "filters"
    ws.append(WORKBOOK_HEADER)
    for row in rows:
        ws.append(list(row[:3]) + [None] * (3 - len(row[:3])) + (list(row[3:]) or LOCATION))
    if mapping is not None:
        types = book.create_sheet(filter_files.SCHEMA_SHEETS["postgres"])
        types.append(["source_data_type", "target_data_type", "precision_check", "notes"])
        for row in mapping:
            types.append(list(row))
    book.save(path)
    return path


@pytest.fixture(autouse=True)
def workbook_folder(monkeypatch, tmp_path_factory):
    """The filter workbook is where each table lives. This one lists the fake tables at the fake location, so 'all' means
    units, notes and lookup in these tests, as the fake source holds them."""
    folder = tmp_path_factory.mktemp("workbooks")
    write_filter_workbook(folder / "edge_bronze_master.xlsx", [["units"], ["notes"], ["lookup"]])
    monkeypatch.setattr(filter_files, "FOLDER", folder)
    return folder


# --- resolving the arguments -----------------------------------------------------------------------------------

def test_the_env_placeholder_is_filled_in_to_read_and_kept_to_write():
    args = make_args()
    assert (args.target_database, args.target_template) == ("DEV_EDGE_BRONZE", "{env}_EDGE_BRONZE")
    assert make_args(environment="prod").target_database == "PRD_EDGE_BRONZE"


def test_local_reads_the_dev_database_and_writes_the_placeholder():
    args = make_args(environment="local")
    assert (args.target_database, args.target_template) == ("DEV_EDGE_BRONZE", "{env}_EDGE_BRONZE")


def test_a_target_database_without_the_placeholder_is_read_and_written_as_it_is():
    args = make_args(target_template="ANALYTICS_DB")
    assert (args.target_database, args.target_template) == ("ANALYTICS_DB", "ANALYTICS_DB")


def test_no_location_is_a_default():
    """The flags are checked before any workbook is read: no schema or Snowflake name comes from a default."""
    args = make_args(target_template=None, source_schema=None, target_schema=None)
    assert (args.source_schema, args.target_schema, args.target_database) == (None, None, None)


def test_an_unknown_environment_is_refused():
    with pytest.raises(ValueError, match="unknown --environment uat"):
        make_args(environment="uat")


def test_all_cannot_be_combined_with_table_names():
    with pytest.raises(ValueError, match="'all' cannot be combined"):
        make_args(tables=("all", "units"))


def test_nothing_to_generate_is_refused():
    with pytest.raises(ValueError, match="nothing to generate"):
        make_args(data="no", count="no")


# --- a historical run ------------------------------------------------------------------------------------------

def test_historical_run_writes_the_expected_kinds_and_statuses(tmp_path):
    config = config_dir_for(tmp_path, "historical")
    results = run_generate(make_args("historical"), config)
    assert statuses(results) == {
        ("lookup", "data_validation"): "FAILED", ("lookup", "count_validation"): "NEW",
        ("notes", "data_validation"): "NEW", ("notes", "count_validation"): "NEW",
        ("units", "data_validation"): "NEW", ("units", "count_validation"): "NEW",
    }
    assert detail_of(results, "lookup", "data_validation").startswith(
        "unable to find the primary key of edge.lookup in the source")


def test_a_table_with_no_primary_key_fails_its_data_and_still_counts(tmp_path, caplog):
    config = config_dir_for(tmp_path, "historical")
    with caplog.at_level(logging.ERROR):
        results = run_generate(make_args("historical", tables=("lookup",)), config)
    assert statuses(results) == {("lookup", "data_validation"): "FAILED", ("lookup", "count_validation"): "NEW"}
    assert not (config / "data_validation" / "lookup.yaml").exists(), "no data YAML without a key"
    assert "lookup" in yb._count_tables(config, "bronze_postgres"), "the count needs no key"
    assert "unable to find the primary key of edge.lookup" in caplog.text


def test_the_yaml_holds_the_env_placeholder_on_the_target_side_only(tmp_path):
    config = config_dir_for(tmp_path, "historical")
    run_generate(make_args("historical"), config)
    block = data_block(config, "units")
    count = count_block(config, "bronze_postgres", "units")
    assert block["target_database"] == count["target_database"] == "{env}_EDGE_BRONZE"
    assert "FROM {env}_EDGE_BRONZE." in block["targetquery"] and "FROM {env}_EDGE_BRONZE." in count["targetquery"]
    assert "{" not in block["sourcequery"] and "{" not in count["sourcequery"]
    for text in ((config / "data_validation" / "units.yaml").read_text(encoding="utf-8"),
                 (config / "count_validation" / "bronze_postgres.yaml").read_text(encoding="utf-8")):
        assert "DEV_EDGE_BRONZE" not in text, "the environment is filled in by the validators, not written here"


def test_historical_data_file_drops_the_global_exclusions_and_lists_them_in_the_header(tmp_path):
    config = config_dir_for(tmp_path, "historical")
    run_generate(make_args("historical"), config)
    assert "_fivetran" not in data_block(config, "units")["sourcequery"].lower()
    header = (config / "data_validation" / "units.yaml").read_text(encoding="utf-8")
    assert "# Key: id_normalized   Columns compared: 4" in header
    assert "# Excluded (exclusions file): _fivetran_synced, _fivetran_active" in header


def test_second_historical_run_leaves_every_file_unchanged(tmp_path):
    config = config_dir_for(tmp_path, "historical")
    run_generate(make_args("historical"), config)
    results = run_generate(make_args("historical"), config)
    got = statuses(results)
    assert got[("units", "data_validation")] == "UNCHANGED"
    assert got[("notes", "data_validation")] == "UNCHANGED"
    assert got[("units", "count_validation")] == "UNCHANGED"


def test_a_source_column_with_no_target_match_is_compared_as_missing_not_dropped(tmp_path):
    """A column with no match in Snowflake stays in the comparison. The Snowflake side holds a token, so every row differs."""
    config = config_dir_for(tmp_path, "historical")
    run_generate(make_args("historical"), config)
    changed = {**SOURCE_TABLES, ("edge", "units"): SOURCE_TABLES[("edge", "units")] + [("note_text", "text")]}
    results = run_generate(make_args("historical"), config, source=FakeDB(changed, PRIMARY_KEYS))
    got = statuses(results)
    assert got[("units", "data_validation")] == "UPDATED"
    assert got[("units", "count_validation")] == "UNCHANGED"
    block = data_block(config, "units")
    assert "note_text_normalized" in block["sourcequery"]
    assert f"CAST('{yb.MISSING_IN_TARGET}' AS STRING) AS note_text_normalized" in block["targetquery"]
    assert (f"# Source columns not in Snowflake (compared as {yb.MISSING_IN_TARGET}, so every row differs): note_text"
            in (config / "data_validation" / "units.yaml").read_text(encoding="utf-8"))


def test_a_snowflake_column_with_no_source_match_is_compared_as_missing_not_dropped(tmp_path):
    config = config_dir_for(tmp_path, "historical")
    run_generate(make_args("historical"), config)
    target = {**TARGET_TABLES, ("EDGE", "UNITS"): TARGET_TABLES[("EDGE", "UNITS")] + [("EXTRA_FLAG", "BOOLEAN")]}
    results = run_generate(make_args("historical"), config, target=FakeDB(target))
    assert statuses(results)[("units", "data_validation")] == "UPDATED"
    block = data_block(config, "units")
    assert f"CAST('{yb.MISSING_IN_SOURCE}' AS TEXT) AS extra_flag_normalized" in block["sourcequery"]
    assert '"EXTRA_FLAG"' in block["targetquery"]
    assert (f"# Snowflake columns not in source (compared as {yb.MISSING_IN_SOURCE}, so every row differs): EXTRA_FLAG"
            in (config / "data_validation" / "units.yaml").read_text(encoding="utf-8"))


def test_the_global_list_is_what_drops_a_column(tmp_path):
    config = config_dir_for(tmp_path, "historical")
    run_generate(make_args("historical"), config, global_excluded=set())
    assert "_fivetran_synced_normalized" in data_block(config, "units")["sourcequery"]


def test_a_table_rule_excludes_a_column_and_filters_both_sides_for_that_table_only(tmp_path):
    config = config_dir_for(tmp_path, "historical")
    rules = {"units": TableRule(exclude={"unit_name"}, source_where="id > 0", target_where="ID > 0")}
    run_generate(make_args("historical"), config, table_rules=rules)
    block = data_block(config, "units")
    assert "unit_name" not in block["sourcequery"].lower()
    assert "WHERE (id > 0)" in block["sourcequery"] and "(ID > 0)" in block["targetquery"]
    assert count_block(config, "bronze_postgres", "units")["sourcequery"].endswith("WHERE (id > 0);")
    assert "# Excluded (table rule): unit_name" in (config / "data_validation" / "units.yaml").read_text(encoding="utf-8")
    assert "WHERE (" not in data_block(config, "notes")["sourcequery"], "the rule is for one table only"


def test_an_excluded_key_column_fails_the_data_file_and_still_counts(tmp_path):
    results = run_generate(make_args("historical"), config_dir_for(tmp_path, "historical"),
                              table_rules={"notes": TableRule(exclude={"id"})})
    assert statuses(results)[("notes", "data_validation")] == "FAILED"
    assert detail_of(results, "notes", "data_validation").startswith(
        "key column(s) not on both sides of the comparison: id")
    assert statuses(results)[("notes", "count_validation")] == "NEW"


def test_a_key_rule_gives_a_key_to_a_table_without_one(tmp_path):
    config = config_dir_for(tmp_path, "historical")
    results = run_generate(make_args("historical"), config, table_rules={"lookup": TableRule(key_columns=["code"])})
    assert statuses(results)[("lookup", "data_validation")] == "NEW"
    assert data_block(config, "lookup")["sourcecolumn"] == "code_normalized"


def test_one_table_failing_does_not_stop_the_others(tmp_path):
    config = config_dir_for(tmp_path, "historical")
    source = FakeDB(SOURCE_TABLES, PRIMARY_KEYS, fail_on=("notes",))
    results = run_generate(make_args("historical"), config, source=source)
    got = statuses(results)
    assert got[("notes", "table")] == "FAILED"
    assert got[("units", "data_validation")] == "NEW"
    assert set(yb._count_tables(config, "bronze_postgres")) == {"units", "lookup"}, "notes failed; lookup has no key but still counts"


def test_a_table_missing_from_the_source_is_an_error_and_its_yaml_is_kept(tmp_path):
    config = config_dir_for(tmp_path, "historical")
    run_generate(make_args("historical", tables=("notes",)), config)
    gone = FakeDB({k: v for k, v in SOURCE_TABLES.items() if k[1] != "notes"}, PRIMARY_KEYS)
    results = run_generate(make_args("historical", tables=("notes",)), config, source=gone)
    assert statuses(results)[("notes", "table")] == "FAILED"
    assert "nothing was changed" in detail_of(results, "notes", "table")
    assert (config / "data_validation" / "notes.yaml").exists(), "nothing is deleted"
    assert "notes" in yb._count_tables(config, "bronze_postgres")


def test_a_table_missing_from_snowflake_is_an_error_and_nothing_is_deleted(tmp_path):
    config = config_dir_for(tmp_path, "historical")
    run_generate(make_args("historical"), config)
    target = FakeDB({k: v for k, v in TARGET_TABLES.items() if k[1] != "NOTES"})
    results = run_generate(make_args("historical"), config, target=target)
    assert statuses(results)[("notes", "table")] == "FAILED"
    assert (config / "data_validation" / "notes.yaml").exists()
    assert "notes" in yb._count_tables(config, "bronze_postgres")


def test_all_leaves_a_generated_table_that_left_the_source_alone(tmp_path, caplog):
    config = config_dir_for(tmp_path, "historical")
    run_generate(make_args("historical"), config)
    gone = FakeDB({k: v for k, v in SOURCE_TABLES.items() if k[1] != "notes"}, PRIMARY_KEYS)
    results = run_generate(make_args("historical"), config, source=gone)
    assert not any(status == "REMOVED" for _, _, status, _ in results)
    assert (config / "data_validation" / "notes.yaml").exists()
    assert "notes" in yb._count_tables(config, "bronze_postgres")


def test_all_never_deletes_a_file_the_generator_did_not_write(tmp_path):
    config = config_dir_for(tmp_path, "historical")
    manual = config / "data_validation" / "manual.yaml"
    manual.parent.mkdir(parents=True)
    manual.write_text("tables: {}\n", encoding="utf-8")
    run_generate(make_args("historical"), config)
    assert manual.exists()


# --- an incremental run writes to its own folder -----------------------------------------------------------------

def test_an_incremental_run_does_not_touch_the_historical_folder(tmp_path):
    run_generate(make_args("historical"), config_dir_for(tmp_path, "historical"))
    before = (config_dir_for(tmp_path, "historical") / "data_validation" / "units.yaml").read_text(encoding="utf-8")
    run_generate(make_args("incremental"), config_dir_for(tmp_path, "incremental"))
    assert (config_dir_for(tmp_path, "historical") / "data_validation" / "units.yaml").read_text(encoding="utf-8") == before


# --- row filters from the workbooks ------------------------------------------------------------------------------

def test_a_rules_target_where_with_the_env_placeholder_is_written_as_it_is(tmp_path):
    """The rules workbook accepts {env} in target_where. The target query carries it as written."""
    path = tmp_path / "rules.xlsx"
    book = Workbook()
    ws = book.active
    ws.title = settings.RULES_SHEET
    ws.append(settings.RULE_HEADERS)
    ws.append(["postgres", None, "units", None, None, None, "ID IN (SELECT ID FROM {env}_EDGE_BRONZE.EDGE.NOTES)", None])
    book.save(path)
    rules = settings.load_table_rules(path, "postgres")
    config = config_dir_for(tmp_path, "historical")
    results = run_generate(make_args("historical", tables=("units",)), config, table_rules=rules)
    assert statuses(results)[("units", "data_validation")] == "NEW"
    assert "(ID IN (SELECT ID FROM {env}_EDGE_BRONZE.EDGE.NOTES))" in data_block(config, "units")["targetquery"]


def test_a_file_filter_with_the_env_placeholder_on_the_target_side_is_written_and_checked(tmp_path):
    """The filter workbook keeps {env} on the Snowflake side. The target query holds it, and _checked() accepts it."""
    write_filter_workbook(tmp_path / "edge_bronze_master.xlsx",
                          [["units", "units j\njoin notes n on j.note_id = n.id\nwhere n.body is not null"]])
    file_filters = filter_files.filters(filter_files.read("postgres", "historical", folder=tmp_path, log=LOG), log=LOG)
    config = config_dir_for(tmp_path, "historical")
    results = run_generate(make_args("historical", tables=("units",)), config, file_filters=file_filters)
    assert statuses(results)[("units", "data_validation")] == "NEW", "a target query that fails _checked() would be FAILED"
    assert ('note_id IN (SELECT id FROM {env}_EDGE_BRONZE."EDGE"."NOTES" WHERE body is not null AND _FIVETRAN_ACTIVE = TRUE)'
            in data_block(config, "units")["targetquery"])


# --- run_for_validation: the entry point main.py calls ---------------------------------------------------------

@pytest.fixture
def run_env(monkeypatch, tmp_path):
    """Points the entry point at a temporary folder and fake databases. Returns the folder and a rules workbook."""
    monkeypatch.setattr(generate_yaml, "FRAMEWORK_ROOT", tmp_path)
    dbs ={"source": FakeDB(SOURCE_TABLES, PRIMARY_KEYS), "target": FakeDB(TARGET_TABLES)}
    monkeypatch.setattr(generate_yaml, "get_database",
                        lambda db_type, root, env: dbs["source"] if db_type == "postgres" else dbs["target"])
    monkeypatch.setattr(filter_files, "filters", lambda *a, **k: {})
    rules = tmp_path / "rules.xlsx"
    book = Workbook()
    ws = book.active
    ws.title = settings.RULES_SHEET
    ws.append(settings.RULE_HEADERS)
    ws.append(["postgres", None, "units", None, None, None, "ID > 5", None])
    book.save(rules)
    return SimpleNamespace(root=tmp_path, dbs=dbs, rules=rules)


def _run(env, **overrides):
    options = dict(layer_type="bronze_postgres", tables=["all"], environment="dev", run_type="historical",
                   data_validation="yes", count_validation="yes", rules_file=env.rules, use_filter_files=False, log=LOG)
    options.update(overrides)
    return generate_yaml.run_for_validation(**options)


def test_all_takes_the_table_names_from_the_workbook_not_the_source(run_env):
    run_env.dbs["source"].tables[("edge", "extra")] = [("id", "integer")]
    results, _ = _run(run_env)
    assert not any(table == "extra" for table, *_ in results), "a source table that no workbook lists is not generated"
    assert statuses(results)[("units", "data_validation")] == "NEW"


def test_no_table_in_the_workbooks_is_refused_before_anything_is_read(run_env, monkeypatch, tmp_path):
    empty = tmp_path / "empty"
    empty.mkdir()
    monkeypatch.setattr(filter_files, "FOLDER", empty)
    with pytest.raises(ValueError, match="the filter workbook for --layer_type bronze_postgres is missing"):
        _run(run_env)
    assert run_env.dbs["source"].queries == [], "no catalogue is read without a workbook row"


def test_a_table_name_in_another_case_is_the_workbooks_table(run_env):
    results, ready = _run(run_env, tables=["UNITS"])
    assert ready == ["units"] and statuses(results)[("units", "data_validation")] == "NEW"


def test_a_named_table_with_no_workbook_row_fails_and_the_others_go_on(run_env):
    results, ready = _run(run_env, tables=["units", "orders"])
    assert statuses(results)[("orders", "table")] == "FAILED"
    assert detail_of(results, "orders", "table").startswith("no row in the filter workbooks for PostgreSQL")
    assert ready == ["units"]


def test_the_schemas_and_the_snowflake_name_come_from_the_workbook_row(run_env):
    _run(run_env, tables=["units"])
    block = data_block(config_dir_for(run_env.root, "historical"), "units")
    assert (block["source_schema"], block["target_database"], block["target_schema"]) == \
        (SOURCE_SCHEMA, "{env}_EDGE_BRONZE", TARGET_SCHEMA)
    assert any("DEV_EDGE_BRONZE.INFORMATION_SCHEMA" in sql for sql in run_env.dbs["target"].queries), \
        "the dev run reads the Snowflake catalogue with {env} filled in"


def test_two_applications_on_one_engine_are_generated_each_at_its_own_location(run_env, workbook_folder):
    """Edge and Payments are both PostgreSQL. One run reads each table from its own row's schema and Snowflake database."""
    run_env.dbs["source"].tables[("payments", "charges")] = [("id", "integer"), ("amount", "integer")]
    run_env.dbs["source"].primary_keys[("payments", "charges")] = ["id"]
    run_env.dbs["target"].tables[("PAYMENTS", "CHARGES")] = [("ID", "NUMBER"), ("AMOUNT", "NUMBER")]
    write_filter_workbook(workbook_folder / "edge_bronze_master.xlsx",
                          [["units"], ["charges", None, None, "Payments", "fms", "payments", "Snowflake",
                                       "{env}_PAYMENTS_BRONZE", "PAYMENTS"]])
    results, ready = _run(run_env, tables=["units", "charges"])
    assert set(ready) == {"units", "charges"}
    block = data_block(config_dir_for(run_env.root, "historical"), "charges")
    assert (block["source_schema"], block["target_database"], block["target_schema"]) == \
        ("payments", "{env}_PAYMENTS_BRONZE", "PAYMENTS")


def test_a_schema_that_lists_no_table_fails_its_own_tables_and_the_other_location_goes_on(run_env, workbook_folder):
    write_filter_workbook(workbook_folder / "edge_bronze_master.xlsx",
                          [["units"], ["charges", None, None, "Payments", "fms", "nosuch", "Snowflake",
                                       "{env}_PAYMENTS_BRONZE", "PAYMENTS"]])
    results, ready = _run(run_env, tables=["units", "charges"])
    assert statuses(results)[("charges", "table")] == "FAILED"
    assert "no tables visible in source schema nosuch" in detail_of(results, "charges", "table")
    assert ready == ["units"]


def test_a_workbook_row_with_an_empty_location_cell_stops_the_run(run_env, workbook_folder):
    write_filter_workbook(workbook_folder / "edge_bronze_master.xlsx",
                          [["units", None, None, "Edge", "fms", None, "Snowflake", "{env}_EDGE_BRONZE", TARGET_SCHEMA]])
    with pytest.raises(ValueError, match=r"Filter workbook problem: .*row 2 \(units\): source_schema is empty"):
        _run(run_env, tables=["units"])


def test_run_for_validation_returns_results_and_the_tables_ready_to_validate(run_env):
    results, ready = _run(run_env)
    assert statuses(results)[("units", "data_validation")] == "NEW"
    assert set(ready) == {"notes", "units", "lookup"}, "lookup has no primary key: its data FAILED, but its count is ready"
    assert not (run_env.root / "output").exists(), "the generator writes no report, only the YAML"


def test_a_key_present_only_on_the_source_side_fails_the_data_and_the_count_is_ready(run_env):
    columns = [c for c in TARGET_TABLES[("EDGE", "UNITS")] if c[0] != "ID"]
    run_env.dbs["target"] = FakeDB({**TARGET_TABLES, ("EDGE", "UNITS"): columns})
    results, ready = _run(run_env, tables=["units"])
    assert statuses(results) == {("units", "data_validation"): "FAILED", ("units", "count_validation"): "NEW"}
    assert detail_of(results, "units", "data_validation").startswith("key column(s) not on both sides of the comparison: id")
    assert not (run_env.root / "config" / "historical" / "bronze_postgres" / "data_validation" / "units.yaml").exists()
    assert ready == ["units"]


def test_a_table_whose_count_file_could_not_be_updated_is_not_ready(run_env, monkeypatch):
    def locked(*args, **kwargs):
        raise OSError("count file locked")
    monkeypatch.setattr(generate_yaml, "upsert_count_file", locked)
    results, ready = _run(run_env)
    assert statuses(results)[("units", "count_validation")] == "FAILED"
    assert "units" not in ready, "validating against an old count file would compare the wrong rows"


def test_run_for_validation_writes_the_table_rule_from_the_workbook(run_env):
    _run(run_env)
    block = data_block(config_dir_for(run_env.root, "historical"), "units")
    assert "(ID > 5)" in block["targetquery"]


def test_a_table_that_failed_is_not_ready(run_env, monkeypatch):
    failing = FakeDB(SOURCE_TABLES, PRIMARY_KEYS, fail_on=("notes",))
    monkeypatch.setattr(generate_yaml, "get_database",
                        lambda db_type, root, env: failing if db_type == "postgres" else run_env.dbs["target"])
    results, ready = _run(run_env)
    assert statuses(results)[("notes", "table")] == "FAILED"
    assert "notes" not in ready and "units" in ready


def test_a_connection_that_cannot_open_is_a_value_error(run_env, monkeypatch):
    def refuse(db_type, root, env):
        raise FileNotFoundError("creds/dev.yaml")
    monkeypatch.setattr(generate_yaml, "get_database", refuse)
    with pytest.raises(ValueError, match="Cannot open the connections"):
        _run(run_env)


def test_a_bad_rules_workbook_is_a_settings_problem(run_env):
    book = Workbook()
    book.active.title = settings.RULES_SHEET
    book.active.append(["table_name", "bogus"])
    book.save(run_env.rules)
    with pytest.raises(ValueError, match="Settings problem"):
        _run(run_env)


def test_a_file_filter_is_used_only_where_the_rules_workbook_has_none(run_env, monkeypatch):
    monkeypatch.setattr(filter_files, "filters", lambda *a, **k: {"units": ("id > 0", "ID > 1")})
    _run(run_env, use_filter_files=True)
    block = data_block(config_dir_for(run_env.root, "historical"), "units")
    assert "(id > 0)" in block["sourcequery"], "no rule for the source side, so the file's filter is used"
    assert "(ID > 5)" in block["targetquery"] and "ID > 1" not in block["targetquery"], "the rules workbook wins"


def test_filter_files_are_not_read_without_the_flag(run_env, monkeypatch):
    def must_not_run(*a, **k):
        raise AssertionError("filters read with --filter_files no")
    monkeypatch.setattr(filter_files, "filters", must_not_run)
    _run(run_env, use_filter_files=False)


def test_an_unsupported_layer_is_a_value_error(run_env):
    """ISSUE-03, fixed: an engine with no db_rules.RULES block is refused by name, before any workbook is read."""
    with pytest.raises(ValueError, match="no engine 'silver'"):
        _run(run_env, layer_type="bronze_silver")


# --- safety: nothing is deleted on a bad listing, or on a flag that does not ask for it ----------------------------

def test_an_empty_source_schema_is_refused_and_deletes_nothing(tmp_path):
    config = config_dir_for(tmp_path, "historical")
    run_generate(make_args("historical"), config)
    empty = FakeDB({}, PRIMARY_KEYS)
    with pytest.raises(ValueError, match="no tables visible in source schema"):
        run_generate(make_args("historical"), config, source=empty)
    assert (config / "data_validation" / "notes.yaml").exists()
    assert "notes" in yb._count_tables(config, "bronze_postgres")


def test_a_run_never_deletes_a_yaml_file_or_a_count_entry(tmp_path):
    config = config_dir_for(tmp_path, "historical")
    run_generate(make_args("historical"), config)
    gone = FakeDB({k: v for k, v in SOURCE_TABLES.items() if k[1] != "notes"}, PRIMARY_KEYS)
    run_generate(make_args("historical", data="no"), config, source=gone)
    assert (config / "data_validation" / "notes.yaml").exists()
    assert "notes" in yb._count_tables(config, "bronze_postgres")


def test_a_count_run_with_count_no_keeps_the_count_entry(tmp_path):
    config = config_dir_for(tmp_path, "historical")
    run_generate(make_args("historical"), config)
    gone = FakeDB({k: v for k, v in SOURCE_TABLES.items() if k[1] != "notes"}, PRIMARY_KEYS)
    run_generate(make_args("historical", count="no", tables=("notes",)), config, source=gone)
    assert "notes" in yb._count_tables(config, "bronze_postgres"), "--count_validation no must not remove the count entry"


def test_an_empty_target_schema_is_refused_and_deletes_nothing(tmp_path):
    # A wrong or mis-cased --target_schema lists no tables in Snowflake. Every generated file would look missing.
    config = config_dir_for(tmp_path, "historical")
    run_generate(make_args("historical"), config)
    with pytest.raises(ValueError, match="no tables visible in Snowflake schema"):
        run_generate(make_args("historical"), config, target=FakeDB({}, PRIMARY_KEYS))
    assert (config / "data_validation" / "notes.yaml").exists()
    assert "notes" in yb._count_tables(config, "bronze_postgres")


def test_a_run_for_another_source_schema_does_not_remove_these_tables(tmp_path):
    config = config_dir_for(tmp_path, "historical")
    run_generate(make_args("historical"), config)
    sales = FakeDB({("sales", "orders"): [("id", "integer")]}, {("sales", "orders"): ["id"]})
    run_generate(make_args("historical", source_schema="sales"), config, source=sales)
    assert (config / "data_validation" / "units.yaml").exists(), "units belongs to schema edge, not sales"
    assert {"units", "notes"} <= set(yb._count_tables(config, "bronze_postgres"))


# --- create-if-missing: a run without --yaml_generation creates a named table's YAML, and leaves the YAML that exists alone

def test_create_only_writes_a_missing_yaml_and_leaves_an_existing_one_as_it_is(tmp_path):
    config = config_dir_for(tmp_path, "historical")
    run_generate(make_args("historical", tables=("notes",)), config)
    before = (config / "data_validation" / "notes.yaml").read_text(encoding="utf-8")
    results = run_generate(make_args("historical", tables=("notes", "units")), config, create_only=True)
    got = statuses(results)
    assert got[("notes", "data_validation")] == "PRESENT" and got[("notes", "count_validation")] == "PRESENT"
    assert got[("units", "data_validation")] == "NEW" and got[("units", "count_validation")] == "NEW"
    assert (config / "data_validation" / "notes.yaml").read_text(encoding="utf-8") == before


def test_create_only_writes_the_count_entry_when_only_that_is_missing(tmp_path):
    config = config_dir_for(tmp_path, "historical")
    run_generate(make_args("historical", tables=("notes",), count="no"), config)
    results = run_generate(make_args("historical", tables=("notes",)), config, create_only=True)
    got = statuses(results)
    assert got[("notes", "data_validation")] == "PRESENT"
    assert got[("notes", "count_validation")] == "NEW"


def test_tables_missing_yaml_lists_only_the_named_tables_with_a_missing_file_or_entry(tmp_path, monkeypatch):
    monkeypatch.setattr(generate_yaml, "FRAMEWORK_ROOT", tmp_path)
    config = config_dir_for(tmp_path, "historical")
    run_generate(make_args("historical", tables=("notes",)), config)
    assert generate_yaml.tables_missing_yaml("bronze_postgres", "historical", ["notes", "units"], "yes", "yes") == ["units"]
    assert generate_yaml.tables_missing_yaml("bronze_postgres", "historical", ["notes", "units"], "no", "no") == []


def test_a_missing_source_table_is_not_created_in_create_only_mode(tmp_path):
    config = config_dir_for(tmp_path, "historical")
    gone = FakeDB({k: v for k, v in SOURCE_TABLES.items() if k[1] != "units"}, PRIMARY_KEYS)
    results = run_generate(make_args("historical", tables=("units",)), config, source=gone, create_only=True)
    assert statuses(results)[("units", "table")] == "FAILED"
    assert not (config / "data_validation" / "units.yaml").exists()


# --- incremental runs: the window, and a table without the column ----------------------------------------------

def test_an_incremental_run_writes_the_window_on_both_sides_of_data_and_count(tmp_path):
    config = config_dir_for(tmp_path, "incremental")
    results = run_generate(make_args("incremental"), config)
    assert statuses(results)[("units", "data_validation")] == "NEW"
    block = data_block(config, "units")
    assert '"updated_at" > :start_time AND "updated_at" <= :end_time' in block["sourcequery"]
    assert '"UPDATED_AT" > :start_time AND "UPDATED_AT" <= :end_time' in block["targetquery"]
    count = count_block(config, "bronze_postgres", "units")
    assert '"updated_at" > :start_time AND "updated_at" <= :end_time' in count["sourcequery"]
    assert '"UPDATED_AT" > :start_time AND "UPDATED_AT" <= :end_time' in count["targetquery"]


def test_a_historical_run_has_no_window(tmp_path):
    config = config_dir_for(tmp_path, "historical")
    run_generate(make_args("historical"), config)
    assert ":start_time" not in data_block(config, "units")["sourcequery"]
    assert ":start_time" not in count_block(config, "bronze_postgres", "units")["sourcequery"]


def test_a_window_column_missing_from_snowflake_is_written_by_name_with_a_warning(tmp_path, caplog):
    config = config_dir_for(tmp_path, "incremental")
    target = FakeDB({("EDGE", "DOCS"): [c for c in DOCS_TARGET[("EDGE", "DOCS")] if c[0] != "UPDATED_AT"]})
    with caplog.at_level(logging.WARNING):
        results = run_generate(make_args("incremental", tables=("docs",)), config,
                                  source=FakeDB(DOCS_SOURCE, {("edge", "docs"): ["id"]}), target=target)
    assert statuses(results) == {("docs", "data_validation"): "NEW", ("docs", "count_validation"): "NEW"}
    block = data_block(config, "docs")
    assert '"updated_at" > :start_time AND "updated_at" <= :end_time' in block["sourcequery"]
    assert '"UPDATED_AT" > :start_time AND "UPDATED_AT" <= :end_time' in block["targetquery"]
    assert count_block(config, "bronze_postgres", "docs")["targetquery"].count(":start_time") == 1
    assert "incremental column updated_at is not compared on the Snowflake side" in caplog.text


def test_the_rules_workbook_can_name_the_incremental_column(tmp_path):
    config = config_dir_for(tmp_path, "incremental")
    run_generate(make_args("incremental"), config, table_rules={"units": TableRule(incremental_column="created_at")})
    assert '"created_at" > :start_time' in data_block(config, "units")["sourcequery"]
    assert '"CREATED_AT" > :start_time' in data_block(config, "units")["targetquery"]


def test_run_for_validation_passes_the_run_type_so_the_incremental_yaml_has_the_window(run_env):
    _run(run_env, run_type="incremental", tables=["units"])
    block = data_block(config_dir_for(run_env.root, "incremental"), "units")
    assert ":start_time" in block["sourcequery"] and ":end_time" in block["targetquery"]


DOCS_SOURCE = {("edge", "docs"): [("id", "integer"), ("meta", "jsonb"), ("tags", "hstore"),
                                  ("updated_at", "timestamp without time zone")]}
DOCS_TARGET = {("EDGE", "DOCS"): [("ID", "NUMBER"), ("META", "VARIANT"), ("TAGS", "TEXT"), ("UPDATED_AT", "TIMESTAMP_NTZ")]}


def test_jsonb_and_hstore_columns_are_listed_for_the_validator_and_selected_as_text(tmp_path):
    config = config_dir_for(tmp_path, "historical")
    source, target = FakeDB(DOCS_SOURCE, {("edge", "docs"): ["id"]}), FakeDB(DOCS_TARGET)
    results = run_generate(make_args("historical", tables=("docs",)), config, source=source, target=target)
    assert statuses(results)[("docs", "data_validation")] == "NEW"
    block = data_block(config, "docs")
    assert block["json_columns"] == {"meta_normalized": "jsonb", "tags_normalized": "hstore"}
    assert 'CAST("meta" AS TEXT)' in block["sourcequery"]
    assert 'CAST("META" AS STRING)' in block["targetquery"]
    assert "# Compared as flattened JSON text (json_columns, both sides): meta_normalized, tags_normalized" in \
        (config / "data_validation" / "docs.yaml").read_text(encoding="utf-8")


# --- the schema validation YAML (--schema_validation yes) --------------------------------------------------------

def schema_doc(root, run_type="historical"):
    path = config_dir_for(root, run_type) / "schema_validation" / "schema_validation.yaml"
    return read_yaml(path)["validations"]["schema_validation"]


def _schema_only(env, **overrides):
    return _run(env, data_validation="no", count_validation="no", schema_validation="yes", **overrides)


def test_a_schema_only_run_writes_the_schema_file_and_no_data_or_count(run_env):
    results, ready = _schema_only(run_env)
    assert statuses(results) == {(t, "schema_validation"): "NEW" for t in ("units", "notes", "lookup")}
    assert set(ready) == {"units", "notes", "lookup"}, "lookup has no primary key, which schema validation does not need"
    config = config_dir_for(run_env.root, "historical")
    assert not (config / "data_validation").exists() and not (config / "count_validation").exists()
    doc = schema_doc(run_env.root)
    assert list(doc["tables"]) == ["units", "notes", "lookup"]
    assert (doc["source"], doc["target"]) == ("postgres", "snowflake")
    assert "where table_schema = 'edge' and table_name = '{table_name}'" in doc["sourcequery"]
    assert "FROM {env}_EDGE_BRONZE.information_schema.columns" in doc["targetquery"], "the validator fills {env} for its run"
    assert "table_schema = 'EDGE' AND TABLE_NAME = '{table_name}'" in doc["targetquery"]
    assert doc["datatype_map"]["timestamp without time zone"] == "timestamp_ntz"
    assert doc["precision_checks"] == {"numeric": ["integer"], "character": ["character varying"],
                                       "datetime": ["timestamp without time zone"]}


def test_the_schema_file_holds_no_exclusions_and_an_excluded_column_needs_no_mapping(run_env, workbook_folder):
    """Exclusions are kept only in the settings (the validator reads them), so the file lists table names only. units'
    _fivetran_synced is timestamp with time zone; it is excluded, so that type needs no mapping row."""
    write_filter_workbook(workbook_folder / "edge_bronze_master.xlsx", [["units"]],
                          mapping=[row for row in TYPE_MAPPING if row[0] != "timestamp with time zone"])
    results, _ = _schema_only(run_env)
    assert statuses(results) == {("units", "schema_validation"): "NEW"}
    assert schema_doc(run_env.root)["tables"] == {"units": {}}


def test_a_second_schema_run_is_unchanged_and_another_environment_too(run_env):
    _schema_only(run_env, tables=["units"])
    results, _ = _schema_only(run_env, tables=["units"])
    assert statuses(results) == {("units", "schema_validation"): "UNCHANGED"}
    results, _ = _schema_only(run_env, tables=["units"], environment="stg")
    assert statuses(results) == {("units", "schema_validation"): "UNCHANGED"}, "one file serves every environment"


def test_catalogue_types_in_capitals_match_the_lower_case_mapping(run_env):
    """The workbook's types match ignoring case, so NUMBER finds 'number'. The validator looks the catalogue's spelling up
    exactly, so that spelling is written into the YAML's maps too."""
    run_env.dbs["source"] = FakeDB({("edge", "units"): [("id", "INTEGER"), ("unit_name", "CHARACTER VARYING"),
                                                         ("updated_at", "TIMESTAMP WITHOUT TIME ZONE")]})
    results, _ = _schema_only(run_env, tables=["units"])
    assert statuses(results) == {("units", "schema_validation"): "NEW"}
    doc = schema_doc(run_env.root)
    assert doc["datatype_map"]["INTEGER"] == "number" and doc["datatype_map"]["integer"] == "number"
    assert doc["datatype_map"]["CHARACTER VARYING"] == "text"
    assert doc["datatype_map"]["TIMESTAMP WITHOUT TIME ZONE"] == "timestamp_ntz"
    assert "INTEGER" in doc["precision_checks"]["numeric"] and "integer" in doc["precision_checks"]["numeric"]
    assert "CHARACTER VARYING" in doc["precision_checks"]["character"]


def test_a_source_type_with_no_mapping_row_fails_that_tables_schema_and_names_it(run_env, workbook_folder, caplog):
    write_filter_workbook(workbook_folder / "edge_bronze_master.xlsx", [["units"], ["notes"], ["lookup"]],
                          mapping=[row for row in TYPE_MAPPING if row[0] != "text"])
    with caplog.at_level(logging.ERROR):
        results, ready = _run(run_env, schema_validation="yes")
    assert statuses(results)[("notes", "schema_validation")] == "FAILED"
    assert "no row in the 'Postgres schema mapping' sheet: body (text)" in detail_of(results, "notes", "schema_validation")
    assert "notes: schema YAML not written" in caplog.text
    assert statuses(results)[("units", "schema_validation")] == "NEW"
    assert "notes" not in schema_doc(run_env.root)["tables"]
    assert "notes" in ready, "a schema FAILED does not stop the data and count of the table"


def test_a_table_the_schema_query_cannot_see_fails_its_schema(run_env):
    source = run_env.dbs["source"]
    real = source.execute_query
    source.execute_query = lambda sql: real(sql).iloc[0:0] if "character_maximum_length" in sql else real(sql)
    results, _ = _schema_only(run_env, tables=["units"])
    assert statuses(results) == {("units", "schema_validation"): "FAILED"}
    assert "returns no column for edge.units" in detail_of(results, "units", "schema_validation")


def test_a_missing_mapping_sheet_stops_the_run_before_anything_is_written(run_env, workbook_folder):
    write_filter_workbook(workbook_folder / "edge_bronze_master.xlsx", [["units"]], mapping=None)
    with pytest.raises(ValueError, match="Filter workbook problem: .*no sheet 'Postgres schema mapping'"):
        _run(run_env, schema_validation="yes")
    assert not (run_env.root / "config").exists()


def test_the_mapping_sheet_is_not_read_without_schema_validation(run_env, workbook_folder):
    write_filter_workbook(workbook_folder / "edge_bronze_master.xlsx", [["units"]], mapping=None)
    results, _ = _run(run_env, tables=["units"])
    assert statuses(results)[("units", "data_validation")] == "NEW"


def test_a_table_at_a_second_location_fails_its_schema_and_keeps_its_data(run_env, workbook_folder):
    run_env.dbs["source"].tables[("payments", "charges")] = [("id", "integer")]
    run_env.dbs["source"].primary_keys[("payments", "charges")] = ["id"]
    run_env.dbs["target"].tables[("PAYMENTS", "CHARGES")] = [("ID", "NUMBER")]
    write_filter_workbook(workbook_folder / "edge_bronze_master.xlsx",
                          [["units"], ["charges", None, None, "Payments", "fms", "payments", "Snowflake",
                                       "{env}_PAYMENTS_BRONZE", "PAYMENTS"]])
    results, ready = _run(run_env, tables=["charges", "units"], schema_validation="yes")
    assert statuses(results)[("charges", "schema_validation")] == "FAILED"
    assert "the schema file holds one location (edge -> {env}_EDGE_BRONZE.EDGE)" in \
        detail_of(results, "charges", "schema_validation")
    assert statuses(results)[("charges", "data_validation")] == "NEW" and "charges" in ready
    assert list(schema_doc(run_env.root)["tables"]) == ["units"]


def test_an_incremental_schema_run_creates_the_incremental_file(run_env):
    _schema_only(run_env, run_type="incremental")
    assert set(schema_doc(run_env.root, "incremental")["tables"]) == {"units", "notes", "lookup"}
    assert not (config_dir_for(run_env.root, "historical") / "schema_validation").exists()


def test_create_only_adds_a_missing_schema_entry_and_leaves_the_others(run_env):
    _schema_only(run_env, tables=["units"])
    results, _ = _schema_only(run_env, tables=["units", "notes"], create_only=True)
    assert statuses(results) == {("units", "schema_validation"): "PRESENT", ("notes", "schema_validation"): "NEW"}


def test_tables_missing_yaml_checks_the_schema_file_when_asked(run_env):
    _schema_only(run_env, tables=["units"])
    assert generate_yaml.tables_missing_yaml("bronze_postgres", "historical", ["UNITS", "notes"], "no", "no", "yes") == ["notes"]
    assert generate_yaml.tables_missing_yaml("bronze_postgres", "historical", ["notes"], "no", "no", "no") == []


def test_all_warns_about_a_schema_entry_no_longer_in_the_workbook_and_keeps_it(run_env, workbook_folder, caplog):
    _schema_only(run_env)
    write_filter_workbook(workbook_folder / "edge_bronze_master.xlsx", [["units"], ["notes"]])
    with caplog.at_level(logging.WARNING):
        _schema_only(run_env)
    assert "The schema file holds lookup, which are not in the filter workbook" in caplog.text
    assert "lookup" in schema_doc(run_env.root)["tables"]


def test_the_unchanged_validator_reads_the_generated_file(run_env, monkeypatch, tmp_path):
    """End to end: validators/schema_validation.py runs on the generated YAML against a fake catalogue. The excluded
    Fivetran columns are not reported missing, and every type is in the map, so the table PASSES. _FIVETRAN_DELETED is not
    in the validator's own hard-coded list, so only the settings (exclusions.yaml, snowflake section) leave it out."""
    import pandas as pd
    from utils.utility import data_context
    from validators import schema_validation as sv

    run_env.dbs["target"].tables[("EDGE", "UNITS")] = TARGET_TABLES[("EDGE", "UNITS")] + [("_FIVETRAN_DELETED", "BOOLEAN")]
    _schema_only(run_env, tables=["units"])
    monkeypatch.setattr(settings, "pick_rules_file", lambda *a, **k: run_env.rules)
    names = ["table_name", "column_name", "data_type", "character_maximum_length", "character_octet_length",
             "numeric_precision", "numeric_precision_radix", "numeric_scale", "datetime_precision"]
    snow_type = {row[0]: row[1] for row in TYPE_MAPPING}

    def row(table, column, dtype, source_type, precision):
        return (table, column, dtype, 255 if source_type == "character varying" else None, None,
                precision if source_type == "integer" else None, None, 0 if source_type == "integer" else None,
                6 if "timestamp" in source_type else None)

    class Catalogue:
        def __init__(self, side):
            self.side = side

        def execute_query(self, sql):
            assert "{" not in sql, "the validator fills every placeholder"
            assert self.side == "source" or "DEV_EDGE_BRONZE.information_schema" in sql
            source = SOURCE_TABLES[("edge", "units")]
            if self.side == "source":
                return pd.DataFrame([row("units", c, t, t, 32) for c, t in source], columns=names)
            target = [row("UNITS", c, snow_type[t].upper(), t, 38) for (c, _), (_, t) in zip(TARGET_TABLES[("EDGE", "UNITS")], source)]
            target.append(row("UNITS", "_FIVETRAN_DELETED", "BOOLEAN", "boolean", 38))
            return pd.DataFrame(target, columns=[n.upper() for n in names])

    monkeypatch.setattr(sv, "get_database", lambda db_type, *a: Catalogue("source" if db_type == "postgres" else "target"))
    out = tmp_path / "out"
    out.mkdir()
    path = config_dir_for(run_env.root, "historical") / "schema_validation" / "schema_validation.yaml"
    ctx = data_context()(run_id="r1", run_at="now", validation_dirs=["schema_validation"], layer_type=["bronze_postgres"],
                         outputpaths={"schema_validation": str(out)}, configpaths={"schema_validation": [str(path)]},
                         BASE_DIR=str(run_env.root), environment="dev", tables=["units"])
    assert sv.validate_schema(ctx) == (0, False)
    report = out / "schema_validation_result_summary.xlsx"
    assert pd.read_excel(report).loc[0, "overall_status"] == "PASS"
    assert "columns_missing_in_source" not in pd.ExcelFile(report).sheet_names
