"""Unit tests for yaml_builder.py: names, filters, reconciliation, the SQL and the files.

Known defects are marked xfail(strict=True) with an ISSUE number. A strict xfail that starts to pass fails the run,
so its marker is removed when the defect is fixed.
"""

import pytest
import yaml

from helpers import STAMP
from yaml_generation import yaml_builder as yb
from yaml_generation.db_rules import RULES

PG, MS = RULES["postgres"], RULES["mssql"]
TEMPLATE = "{env}_EDGE_BRONZE"  # the name the YAML holds. The validators fill in {env} for their run.
SPEC = yb.TableSpec("postgres", "edge", "units", TEMPLATE, "EDGE")


def _pairs(*rows):
    """Reconciliation pairs from (source, source_type, target, target_type) without the normalised name."""
    return [(s, st, t, tt, yb.normalize_name(s)) for s, st, t, tt in rows]


# --- names and filters -----------------------------------------------------------------------------------------

@pytest.mark.parametrize("raw, expected", [
    (" Access Note ", "access_note"),
    ("ACCESS-NOTE", "access_note"),
    ("Created By Import", "created_by_import"),
    ("Site  Id", "site_id"),
])
def test_normalize_name_trims_lowers_and_joins_with_underscores(raw, expected):
    assert yb.normalize_name(raw) == expected


@pytest.mark.parametrize("bad", ["a = 1; DROP TABLE t", "a = 1 -- note", "a = 1 /* note */"])
def test_check_filter_refuses_text_that_breaks_the_sql(bad):
    with pytest.raises(ValueError):
        yb.check_filter(bad, "rules")


def test_check_filter_empty_means_no_filter():
    assert yb.check_filter("   ", "rules") is None
    assert yb.check_filter(None, "rules") is None


@pytest.mark.parametrize("ok", ["is_active = 1", "status = 'true'", "[false] = 1"])
def test_check_filter_sql_server_allows_bit_values_and_quoted_words(ok):
    assert yb.check_filter(ok, "rules", "mssql") == ok


@pytest.mark.parametrize("bad", ["is_active = true", "is_active = FALSE AND x = 1"])
def test_check_filter_sql_server_refuses_true_and_false_words(bad):
    with pytest.raises(ValueError, match="1 or 0"):
        yb.check_filter(bad, "rules", "mssql")


def test_check_filter_keeps_true_for_snowflake_and_postgres():
    assert yb.check_filter("is_active = TRUE", "rules") == "is_active = TRUE"


def test_check_filter_allows_env_only_in_a_target_filter():
    """{env} is the environment placeholder. A source filter refuses it, a target filter accepts it, and any other brace is refused."""
    text = "ID IN (SELECT ID FROM {env}_EDGE_BRONZE.EDGE.NOTES)"
    with pytest.raises(ValueError):
        yb.check_filter(text, "rules", "postgres")
    assert yb.check_filter(text, "rules", placeholders=True) == text
    with pytest.raises(ValueError):
        yb.check_filter("a = {x}", "rules", placeholders=True)


# --- reconciliation --------------------------------------------------------------------------------------------

def test_reconcile_matches_on_normalised_names_only():
    rec = yb.reconcile([("ID", "integer"), ("Access Note", "character varying")],
                       [("id", "NUMBER"), ("ACCESS_NOTE", "TEXT")], set(), set(), PG)
    assert [p[4] for p in rec.pairs] == ["id", "access_note"]
    assert rec.problems == []


def test_reconcile_global_exclusion_status_on_both_sides():
    rec = yb.reconcile([("_FIVETRAN_SYNCED", "text")], [("_FIVETRAN_SYNCED", "TIMESTAMP_NTZ")],
                       {"_fivetran_synced"}, set(), PG)
    assert rec.rows == [("_FIVETRAN_SYNCED", "_FIVETRAN_SYNCED", "EXCLUDED_GLOBAL", "")]
    assert rec.pairs == []


def test_reconcile_table_exclusion_status_on_the_target_side():
    rec = yb.reconcile([("id", "integer")], [("id", "NUMBER"), ("extra_tgt", "TEXT")], set(), {"extra_tgt"}, PG)
    assert ("", "extra_tgt", "EXCLUDED_TABLE", "") in rec.rows


def test_reconcile_global_list_takes_precedence_over_the_table_list():
    rec = yb.reconcile([("x", "text")], [("x", "TEXT")], {"x"}, {"x"}, PG)
    assert rec.rows[0][2] == "EXCLUDED_GLOBAL"


def test_reconcile_reports_missing_and_extra_columns_and_keeps_them_in_the_comparison():
    rec = yb.reconcile([("only_src", "text")], [("only_tgt", "TEXT")], set(), set(), PG)
    assert ("only_src", "", "MISSING_IN_TARGET", "") in rec.rows
    assert ("", "only_tgt", "EXTRA_IN_TARGET", "") in rec.rows
    assert ("only_src", "text", None, None, "only_src") in rec.pairs
    assert (None, None, "only_tgt", "TEXT", "only_tgt") in rec.pairs


def test_a_source_column_of_a_refused_type_on_one_side_only_is_not_a_problem():
    assert yb.reconcile([("Shape", "geography")], [], set(), set(), MS).problems == []


def test_two_source_names_that_normalise_alike_are_a_problem():
    rec = yb.reconcile([("Id", "int"), ("ID", "int")], [("ID", "NUMBER")], set(), set(), MS)
    assert rec.problems == ["'Id' and 'ID' both become 'id'"]


def test_sql_server_geography_cannot_be_compared_as_text():
    rec = yb.reconcile([("Shape", "geography")], [("SHAPE", "TEXT")], set(), set(), MS)
    assert rec.problems == ["'Shape' is type geography, which cannot be compared as text on SQL Server"]
    assert yb.reconcile([("Shape", "geography")], [("SHAPE", "TEXT")], set(), set(), PG).problems == []


@pytest.mark.xfail(strict=True, reason="ISSUE-04: a second target column with the same normalised name is dropped from the report")
def test_every_target_column_appears_in_the_reconciliation_rows():
    rec = yb.reconcile([("id", "integer")], [("ID", "NUMBER"), ("id", "NUMBER")], set(), set(), PG)
    assert sorted(t for _, t, _, _ in rec.rows) == ["ID", "id"]


def test_missing_key_column_is_listed():
    assert yb.missing_key_columns(["id", "only_src"], _pairs(("id", "integer", "ID", "NUMBER"))) == ["only_src"]


def test_a_key_on_one_side_only_is_listed_as_missing():
    assert yb.missing_key_columns(["id"], [("id", "integer", None, None, "id")]) == ["id"]


def test_key_expression_joins_several_keys():
    assert yb.key_expression(["id"]) == "id_normalized"
    assert yb.key_expression(["Company_ID", "Site Id"]) == "company_id_normalized,site_id_normalized"


# --- value expressions -----------------------------------------------------------------------------------------

def test_postgres_bit_is_left_as_it_is_and_sql_server_bit_becomes_text():
    assert yb.value_expression("flags", "bit", PG) == "flags"
    assert yb.value_expression("flags", "bit", MS).startswith("CASE WHEN flags = 1")


def test_sql_server_large_text_is_cast_before_trimming():
    assert yb.value_expression("n", "ntext", MS) == "LTRIM(RTRIM(CAST(n AS NVARCHAR(MAX))))"


def test_text_column_is_trimmed_and_null_becomes_the_token():
    assert yb.value_expression("s", "varchar", MS) == "LTRIM(RTRIM(s))"
    item = yb.normalized_item("Access Note", "access_note_normalized", "character varying", PG)
    assert item == f'COALESCE(CAST(TRIM("Access Note") AS TEXT), \'{yb.NULL_TOKEN}\') AS access_note_normalized'


def test_unknown_type_is_cast_as_it_is_and_reported_as_plain_text():
    pairs = _pairs(("shape", "money", "SHAPE", "TEXT"), ("id", "integer", "ID", "NUMBER"))
    assert yb.plain_text_columns(pairs, PG) == ["shape (money)"]


# --- the SQL of the data and count entries ---------------------------------------------------------------------

def _data_entry(filters=None, pairs=None, keys=("id",)):
    pairs = pairs or _pairs(("id", "integer", "ID", "NUMBER"), ("unit_name", "character varying", "UNIT_NAME", "TEXT"))
    return yb.build_data_entry(SPEC, pairs, list(keys), filters or yb.Filters())


def test_a_column_on_one_side_only_gets_the_token_on_the_side_that_lacks_it():
    pairs = _pairs(("id", "integer", "ID", "NUMBER"), ("gone", "text", None, None)) + [(None, None, "new_col", "TEXT", "new_col")]
    entry = _data_entry(pairs=pairs)
    assert f"CAST('{yb.MISSING_IN_TARGET}' AS STRING) AS gone_normalized" in entry["targetquery"]
    assert f"CAST('{yb.MISSING_IN_SOURCE}' AS TEXT) AS new_col_normalized" in entry["sourcequery"]
    assert '"gone"' in entry["sourcequery"] and '"new_col"' in entry["targetquery"]


def test_target_always_keeps_only_the_active_fivetran_rows():
    assert "WHERE _FIVETRAN_ACTIVE = TRUE;" in _data_entry()["targetquery"]
    count = yb.build_count_entry(SPEC, yb.Filters())
    assert count["targetquery"].endswith("WHERE _FIVETRAN_ACTIVE = TRUE;")


def test_env_is_the_only_brace_in_the_target_sql_and_the_source_has_none():
    """The framework runs str.format() on target queries, so {env} is the only brace allowed there. Source queries have none."""
    filters = yb.Filters(source_where="id > 0", target_where="ID IN (SELECT ID FROM {env}_EDGE_BRONZE.EDGE.NOTES)")
    entry = _data_entry(filters)
    count = yb.build_count_entry(SPEC, filters)
    for target in (entry["targetquery"], count["targetquery"]):
        assert "{env}" in target
        assert "{" not in target.replace("{env}", "") and "}" not in target.replace("{env}", "")
    for source in (entry["sourcequery"], count["sourcequery"]):
        assert "{" not in source and "}" not in source


def test_static_filter_is_wrapped_in_brackets_and_added_after_the_fivetran_rule():
    filters = yb.Filters(source_where="created_at >= '2025-01-01'", target_where="created_at >= '2025-01-01'")
    entry = _data_entry(filters)
    assert entry["sourcequery"].endswith("WHERE (created_at >= '2025-01-01');")
    assert entry["targetquery"].endswith("WHERE _FIVETRAN_ACTIVE = TRUE\n  AND (created_at >= '2025-01-01');")


def test_count_entry_without_filters_is_a_plain_count():
    count = yb.build_count_entry(SPEC, yb.Filters())
    assert count["sourcequery"] == 'SELECT COUNT(*) AS source_row_count FROM "edge"."units";'
    assert count["targetquery"] == ('SELECT COUNT(*) AS target_row_count FROM {env}_EDGE_BRONZE."EDGE"."UNITS" '
                                    'WHERE _FIVETRAN_ACTIVE = TRUE;')


def test_data_entry_round_trips_through_yaml_unchanged():
    entry = _data_entry(yb.Filters(source_where="id > 0"))
    doc = {"tables": {SPEC.table: {"validations": {"data_validation": entry}}}}
    assert yaml.safe_load(yb._dump(doc)) == doc


def test_multi_line_sql_is_written_as_a_block_scalar():
    text = yb._dump({"sourcequery": "SELECT 1\nFROM t;"})
    assert "sourcequery: |" in text


# --- the header ------------------------------------------------------------------------------------------------

def test_header_lists_the_key_the_counts_the_filters_and_the_exclusions():
    rec = yb.reconcile([("id", "integer"), ("unit_name", "character varying"), ("_fivetran_synced", "text"), ("notes", "text")],
                       [("ID", "NUMBER"), ("UNIT_NAME", "TEXT"), ("_FIVETRAN_SYNCED", "TIMESTAMP_NTZ")],
                       {"_fivetran_synced"}, {"notes"}, PG)
    filters = yb.Filters(source_where="id > 0", target_where="ID > 0")
    header = "\n".join(yb.data_header(SPEC, "id_normalized", rec, filters, STAMP))
    assert "# Target: {env}_EDGE_BRONZE.EDGE.UNITS" in header
    assert "# Key: id_normalized   Columns compared: 2" in header
    assert "# Source filter: id > 0" in header and "# Target filter: ID > 0" in header
    assert "# Excluded (global list): _fivetran_synced" in header
    assert "# Excluded (table rule): notes" in header


# --- files: NEW, UNCHANGED, UPDATED, deletion, the count file --------------------------------------------------

def _header(stamp):
    return [f"{yb.GENERATED_MARK} on {stamp}. Do not hand-edit."]


def test_data_file_is_new_then_unchanged_when_only_the_run_time_differs(tmp_path):
    entry = _data_entry()
    _, status = yb.write_data_file(tmp_path, SPEC, entry, _header("2026-01-01T00:00:00"))
    assert status == "NEW"
    _, status = yb.write_data_file(tmp_path, SPEC, entry, _header("2026-02-02T00:00:00"))
    assert status == "UNCHANGED"


def test_data_file_is_updated_when_the_content_changes(tmp_path):
    entry = _data_entry()
    yb.write_data_file(tmp_path, SPEC, entry, _header("2026-01-01T00:00:00"))
    changed = dict(entry, sourcequery=entry["sourcequery"] + " ")
    path, status = yb.write_data_file(tmp_path, SPEC, changed, _header("2026-02-02T00:00:00"))
    assert status == "UPDATED"
    assert path.read_text(encoding="utf-8").startswith(yb.GENERATED_MARK + " on 2026-02-02")


@pytest.mark.xfail(strict=True, reason="ISSUE-01: a hand-written YAML at the same path is overwritten, not refused")
def test_hand_written_data_file_is_not_overwritten(tmp_path):
    path = tmp_path / "data_validation" / "units.yaml"
    path.parent.mkdir(parents=True)
    path.write_text("tables: {units: {}}  # hand written\n", encoding="utf-8")
    with pytest.raises(ValueError):
        yb.write_data_file(tmp_path, SPEC, _data_entry(), _header("2026-01-01T00:00:00"))
    assert "hand written" in path.read_text(encoding="utf-8")





def test_count_file_keeps_other_tables_and_reports_each_status(tmp_path):
    count = yb.build_count_entry(SPEC, yb.Filters())
    other = dict(count, sourcequery="SELECT 2;")
    _, statuses = yb.upsert_count_file(tmp_path, "bronze_postgres", {"units": count, "notes": other})
    assert statuses == {"units": "NEW", "notes": "NEW"}
    _, statuses = yb.upsert_count_file(tmp_path, "bronze_postgres", {"units": count})
    assert statuses == {"units": "UNCHANGED"}
    _, statuses = yb.upsert_count_file(tmp_path, "bronze_postgres", {"units": dict(count, sourcequery="SELECT 3;")})
    assert statuses == {"units": "UPDATED"}
    tables = yb._count_tables(tmp_path, "bronze_postgres")
    assert set(tables) == {"units", "notes"}, "a table not in this run is kept"





def test_count_file_is_not_created_when_there_is_nothing_to_write(tmp_path):
    assert yb.upsert_count_file(tmp_path, "bronze_postgres", {}) is None
    assert not (tmp_path / "count_validation").exists()


def test_generated_tables_are_found_by_mark_or_count_entry_for_the_template(tmp_path):
    count = yb.build_count_entry(SPEC, yb.Filters())
    yb.write_data_file(tmp_path, SPEC, _data_entry(), _header("t"))
    yb.upsert_count_file(tmp_path, "bronze_postgres", {"notes": count})
    assert yb.generated_tables(tmp_path, "bronze_postgres", TEMPLATE, "edge") == {"units", "notes"}
    assert yb.generated_tables(tmp_path, "bronze_postgres", "DEV_EDGE_BRONZE", "edge") == {"units"}, "only the data file's mark matches"
    assert yb.generated_tables(tmp_path, "bronze_postgres", TEMPLATE, "sales") == set(), "another schema's tables are not listed"


def test_a_count_entry_with_a_fixed_database_name_is_reported_as_not_for_this_run(tmp_path):
    """Count entries written before the placeholder held DEV_EDGE_BRONZE. They are reported, not silently kept."""
    fixed = yb.TableSpec("postgres", "edge", "units", "DEV_EDGE_BRONZE", "EDGE")
    yb.upsert_count_file(tmp_path, "bronze_postgres", {"units": yb.build_count_entry(fixed, yb.Filters())})
    assert yb.other_environment_tables(tmp_path, "bronze_postgres", TEMPLATE) == ["units"]
    assert yb.other_environment_tables(tmp_path, "bronze_postgres", "DEV_EDGE_BRONZE") == []


def test_a_filter_with_a_line_break_is_refused():
    # A line break would put the rest of the filter at the top level of the YAML, outside its comment or block.
    with pytest.raises(ValueError):
        yb.check_filter("id > 0\nAND 1 = 1", "rules row 2")


# --- the incremental window and the JSON columns ---------------------------------------------------------------

def test_window_condition_is_greater_than_the_last_run_and_up_to_the_sync():
    assert yb.window_condition('"updated_at"') == '"updated_at" > :start_time AND "updated_at" <= :end_time'


def test_window_is_added_after_the_filters_on_each_side():
    filters = yb.Filters(source_where="id > 0", target_where="ID > 0", source_window="w1", target_window="w2")
    assert filters.source_parts() == ["(id > 0)", "(w1)"]
    assert filters.target_parts() == ["_FIVETRAN_ACTIVE = TRUE", "(ID > 0)", "(w2)"]


def test_incremental_window_needs_the_column_on_both_sides():
    pairs = _pairs(("updated_at", "timestamp without time zone", "UPDATED_AT", "TIMESTAMP_NTZ"), ("id", "integer", "ID", "NUMBER"))
    assert yb.incremental_window(pairs, "Updated At", PG) == (
        '"updated_at" > :start_time AND "updated_at" <= :end_time',
        '"UPDATED_AT" > :start_time AND "UPDATED_AT" <= :end_time')
    assert yb.incremental_window(pairs, "created_at", PG) is None
    assert yb.incremental_window([("updated_at", "text", None, None, "updated_at")], "updated_at", PG) is None


def test_a_data_entry_with_a_window_has_the_placeholders_on_both_sides():
    filters = yb.Filters(source_window='"updated_at" > :start_time AND "updated_at" <= :end_time',
                         target_window='"UPDATED_AT" > :start_time AND "UPDATED_AT" <= :end_time')
    entry = _data_entry(filters)
    assert entry["sourcequery"].endswith('WHERE ("updated_at" > :start_time AND "updated_at" <= :end_time);')
    assert entry["targetquery"].endswith(
        'WHERE _FIVETRAN_ACTIVE = TRUE\n  AND ("UPDATED_AT" > :start_time AND "UPDATED_AT" <= :end_time);')
    count = yb.build_count_entry(SPEC, filters)
    assert count["sourcequery"].endswith('WHERE ("updated_at" > :start_time AND "updated_at" <= :end_time);')


def test_json_columns_are_listed_and_selected_as_text_on_both_sides():
    pairs = _pairs(("id", "integer", "ID", "NUMBER"), ("meta", "jsonb", "META", "VARIANT"), ("tags", "hstore", "TAGS", "TEXT"))
    entry = _data_entry(pairs=pairs)
    assert entry["json_columns"] == {"meta_normalized": "jsonb", "tags_normalized": "hstore"}
    assert 'COALESCE(CAST("meta" AS TEXT), \'<<NULL>>\') AS meta_normalized' in entry["sourcequery"]
    assert 'COALESCE(CAST("META" AS STRING), \'<<NULL>>\') AS meta_normalized' in entry["targetquery"]


def test_a_table_without_json_columns_has_no_json_key():
    assert "json_columns" not in _data_entry()


def test_header_names_the_window_and_the_flattened_columns():
    rec = yb.reconcile([("id", "integer"), ("meta", "jsonb")], [("ID", "NUMBER"), ("META", "VARIANT")], set(), set(), PG)
    header = "\n".join(yb.data_header(SPEC, "id_normalized", rec, yb.Filters(source_window="w1", target_window="w2"), STAMP))
    assert "# Incremental window (source): w1" in header and "# Incremental window (target): w2" in header
    assert "# Compared as flattened JSON text (json_columns, both sides): meta_normalized" in header
