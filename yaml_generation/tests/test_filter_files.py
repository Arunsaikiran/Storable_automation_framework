"""Unit tests for filter_files.py: the statement reader, the workbook reader (_statements) and load() for each run type."""

import logging

import pytest
from openpyxl import Workbook

from yaml_generation import filter_files as ff

ARGS = ("postgres", "public", "DEV_EDGE_BRONZE", "STOREDGE_FMS_PUBLIC")


def _workbook(path, sheets):
    """sheets = {sheet_name: [header_row, data_row, ...]}"""
    book = Workbook()
    book.remove(book.active)
    for name, rows in sheets.items():
        ws = book.create_sheet(name)
        for row in rows:
            ws.append(row)
    book.save(path)
    return path


# --- the statement reader --------------------------------------------------------------------------------------

def test_join_becomes_a_sub_select_on_the_foreign_key():
    src, tgt = ff.convert(
        "select count(*) from  general_ledger_line_items  j\njoin  facilities f on j.facility_id=f.id \n"
        "where f.company_id in (137,9088,501) and j.created_at >=  '2024-08-01';", *ARGS)
    assert src == ("created_at >= '2024-08-01' AND facility_id IN "
                   '(SELECT id FROM "public"."facilities" WHERE company_id in (137,9088,501))')
    assert tgt == ("created_at >= '2024-08-01' AND facility_id IN "
                   '(SELECT id FROM DEV_EDGE_BRONZE."STOREDGE_FMS_PUBLIC"."FACILITIES" '
                   "WHERE company_id in (137,9088,501) AND _FIVETRAN_ACTIVE = TRUE)")


def test_join_written_with_the_id_on_the_left_is_read_the_same():
    src, _ = ff.convert("select count(*) from events j join facilities f on f.id = j.facility_id where f.company_id = 1", *ARGS)
    assert src == 'facility_id IN (SELECT id FROM "public"."facilities" WHERE company_id = 1)'


def test_chained_joins_nest_one_sub_select_inside_the_other():
    src, _ = ff.convert(
        "select count(*) from phone_numbers  j join  tenants  t on j.owner_id=t.id join  facilities  f on t.facility_id=f.id "
        "where  j.owner_type in ( 'Tenant') and f.company_id in (137,9088,501)", *ARGS)
    assert src == ("owner_type in ( 'Tenant') AND owner_id IN (SELECT id FROM \"public\".\"tenants\" WHERE "
                   'facility_id IN (SELECT id FROM "public"."facilities" WHERE company_id in (137,9088,501)))')


def test_join_with_no_condition_on_the_joined_table_keeps_rows_with_a_parent():
    src, _ = ff.convert("select count(*) from discount_plan_ledger_instances_line_items j join discount_plan_ledger_instances ss "
                        "on j.discount_plan_ledger_instance_id=ss.id where created_at >=  '2026-08-01';", *ARGS)
    assert src == ("created_at >= '2026-08-01' AND discount_plan_ledger_instance_id IN "
                   '(SELECT id FROM "public"."discount_plan_ledger_instances")')


def test_a_plain_condition_on_one_table_and_no_filter_at_all():
    assert ff.convert("select count(*) from account_balance_items \nwhere   created_at >=  '2026-08-01';", *ARGS)[0] \
        == "created_at >= '2026-08-01'"
    assert ff.convert("select count(*) from invoiceable_items", *ARGS) == (None, None)


def test_quoted_text_is_read_as_text_not_as_sql():
    src, _ = ff.convert("select count(*) from events where status = 'x or y.z'", *ARGS)
    assert src == "status = 'x or y.z'"


@pytest.mark.parametrize("bad", [
    "select count(*) from transactions j join facilities f on j.facility_id=f.id where f.company_id in (137) "
    "and j.created_at >=  2025-08-01';",                                              # unbalanced quote (a typo)
    "select count(*) from events j join facilities f on j.facility_id=f.id where f.company_id = 1 or j.created_at > '2025-01-01'",
    "select count(*) from events j left join facilities f on j.facility_id=f.id where f.company_id = 1",
    "select count(*) from events j join facilities f on j.facility_id=f.id where j.created_at > f.created_at",
    "select count(*) from events j join facilities f on j.facility_id=f.name where f.company_id = 1",
    "select count(*) from events where status between 'a' and 'b'",
    "select count(*) from events where id in (select id from other)",
    "select count(*) from events e join events e on e.id=e.id",
    "delete from events where created_at > '2025-01-01'",
])
def test_statements_outside_the_reader_are_refused(bad):
    with pytest.raises(ff.Refused):
        ff.convert(bad, *ARGS)


def test_a_comment_marker_is_read_through_and_check_filter_refuses_it():
    src, _ = ff.convert("select count(*) from events j where j.created_at > '2025-01-01' -- note", *ARGS)
    with pytest.raises(ValueError, match="--"):
        ff.check_filter(src, "x")


def test_a_workbook_cell_gets_the_count_prefix_and_a_select_is_kept():
    assert ff._count_statement("events j\nwhere j.x = 1") == "select count(*) from events j\nwhere j.x = 1"
    assert ff._count_statement("select count(*) from events") == "select count(*) from events"


# --- the workbook reader (_statements) -------------------------------------------------------------------------

def test_statements_reads_the_column_for_the_run_type_from_the_first_sheet_that_has_both(tmp_path):
    path = _workbook(tmp_path / "f.xlsx", {
        "notes": [["Something else"], ["x"]],
        "filters": [["  Target Table Name ", "Incremental filters to apply", "Other"],
                    ["units", "created_at > '2025-01-01'", "ignored"],
                    [None, None, None],
                    ["notes", None, "ignored"]],
    })
    assert ff._statements(path, "incremental filters to apply") == [
        (2, "units", "created_at > '2025-01-01'"),
        (4, "notes", ""),
    ]


def test_statements_returns_none_when_no_sheet_has_the_column(tmp_path):
    path = _workbook(tmp_path / "f.xlsx", {"only": [["Target Table Name", "Other"], ["units", "x"]]})
    assert ff._statements(path, "historical filter to apply.") is None


def test_statements_skips_a_row_with_no_table_name_and_keeps_the_row_numbers(tmp_path):
    path = _workbook(tmp_path / "f.xlsx", {"s": [["Target Table Name", "Historical Filter to apply."],
                                                 [None, "x = 1"], ["units", "y = 2"]]})
    assert ff._statements(path, "historical filter to apply.") == [(3, "units", "y = 2")]


# --- load(): the run type picks the column -------------------------------------------------------------------------

@pytest.fixture
def folder(tmp_path):
    _workbook(tmp_path / "postgres.xlsx", {"filters": [
        ["Target Table Name", "Incremental filters to apply", "Historical Filter to apply."],
        ["units", "units\nwhere created_at >= '2025-01-01'", "units\nwhere created_at >= '2020-01-01'"],
        ["notes", "", "notes\nwhere body is not null"],
        ["events", "events j\nwhere j.created_at > '2025-06-01' -- note", None],
        ["bad", "bad\nwhere a = 1 or b = 2", None],
    ]})
    return tmp_path


def test_incremental_run_reads_the_incremental_column(folder):
    filters = ff.load("postgres", "public", "DEV_EDGE_BRONZE", "STOREDGE", "incremental", folder=folder,
                      log=logging.getLogger("t"))
    assert filters["units"] == ("created_at >= '2025-01-01'", "created_at >= '2025-01-01'")
    assert "notes" not in filters, "an empty cell means no filter"


def test_historical_run_reads_the_historical_column(folder):
    filters = ff.load("postgres", "public", "DEV_EDGE_BRONZE", "STOREDGE", "historical", folder=folder,
                      log=logging.getLogger("t"))
    assert filters["units"][0] == "created_at >= '2020-01-01'"
    assert filters["notes"] == ("body is not null", "body is not null")
    assert "units" in filters and "events" not in filters


def test_a_refused_row_loads_its_table_in_full_and_is_logged(folder, caplog):
    with caplog.at_level(logging.WARNING):
        filters = ff.load("postgres", "public", "DEV", "S", "incremental", folder=folder, log=logging.getLogger("t"))
    assert "bad" not in filters
    assert "loaded in full" in caplog.text and "row 5 (bad)" in caplog.text


def test_a_filter_refused_by_check_filter_is_not_applied(folder, caplog):
    with caplog.at_level(logging.WARNING):
        filters = ff.load("postgres", "public", "DEV", "S", "incremental", folder=folder, log=logging.getLogger("t"))
    assert "events" not in filters
    assert "loaded in full" in caplog.text


def test_a_table_listed_twice_keeps_the_first_row(tmp_path):
    _workbook(tmp_path / "a.xlsx", {"s": [["Target Table Name", "Incremental filters to apply"],
                                           ["units", "units\nwhere id > 1"], ["units", "units\nwhere id > 2"]]})
    filters = ff.load("postgres", "public", "DEV", "S", "incremental", folder=tmp_path, log=logging.getLogger("t"))
    assert filters["units"] == ("id > 1", "id > 1")


def test_other_source_types_read_no_filter_files(folder):
    assert ff.load("mssql", "dbo", "DEV", "S", "incremental", folder=folder, log=logging.getLogger("t")) == {}


def test_the_shipped_workbooks_load_for_both_run_types():
    """Read-only smoke test on the real folder. It checks the read does not fail, not what the filters say."""
    for run_type in ("incremental", "historical"):
        assert isinstance(ff.load("postgres", "public", "DEV_EDGE_BRONZE", "STOREDGE", run_type, log=logging.getLogger("t")), dict)


def test_a_cell_about_another_table_is_refused_and_the_table_loaded_in_full(tmp_path, caplog):
    # A cell copied from another row would filter the wrong table. The row's table must be the one the statement reads.
    _workbook(tmp_path / "a.xlsx", {"s": [["Target Table Name", "Incremental filters to apply"],
        ["discount_plan_ledger_instances", "discount_plan_ledger_instances_line_items j\n"
                                           "join discount_plan_ledger_instances ss on j.discount_plan_ledger_instance_id=ss.id\n"
                                           "where j.created_at >= '2026-08-01'"]]})
    with caplog.at_level(logging.WARNING):
        filters = ff.load("postgres", "public", "DEV", "S", "incremental", folder=tmp_path, log=logging.getLogger("t"))
    assert "discount_plan_ledger_instances" not in filters
    assert "is about 'discount_plan_ledger_instances_line_items', not 'discount_plan_ledger_instances'" in caplog.text


def test_a_refused_first_row_does_not_block_a_valid_second_row(tmp_path):
    _workbook(tmp_path / "a.xlsx", {"s": [["Target Table Name", "Incremental filters to apply"],
                                           ["units", 'units\nwhere "id" > 1'], ["units", "units\nwhere id > 2"]]})
    filters = ff.load("postgres", "public", "DEV", "S", "incremental", folder=tmp_path, log=logging.getLogger("t"))
    assert filters["units"] == ("id > 2", "id > 2")


def test_quoted_identifiers_are_refused_as_the_docstring_says():
    with pytest.raises(ff.Refused, match="quoted identifiers"):
        ff.convert('select count(*) from events j join facilities f on j.facility_id=f.id where f."company_id" in (137)', *ARGS)
