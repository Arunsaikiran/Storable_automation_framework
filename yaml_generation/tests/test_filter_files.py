"""Unit tests for filter_files.py: the statement reader, the workbook reader (read: each table's location) and filters()."""

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
    "select count(*) from events j join facilities f on j.facility_id=j.id where f.company_id = 1",
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
    assert ff._count_statement("events j\nwhere j.x = 1", "events") == "select count(*) from events j\nwhere j.x = 1"
    assert ff._count_statement("select count(*) from events", "events") == "select count(*) from events"


def test_a_cell_with_only_conditions_is_about_the_rows_table():
    assert ff._count_statement("created_at >= '2026-08-01'", "account_balance_items") \
        == "select count(*) from account_balance_items where created_at >= '2026-08-01'"
    assert ff.convert(ff._count_statement("created_at >= '2026-08-01'", "account_balance_items"), *ARGS) \
        == ("created_at >= '2026-08-01'", "created_at >= '2026-08-01'")


# --- the workbook reader: read() gives each table's location and filter cell ------------------------------------------

LOG = logging.getLogger("t")
EDGE = ["Edge", "fms", "public", "Snowflake", "{env}_EDGE_BRONZE", "STOREDGE_FMS_PUBLIC"]
SITELINK = ["SiteLink", "DevT5000", "dbo", "Snowflake", "{env}_SITELINK_BRONZE", "SITELINK"]
HEADER = ["Target Table Name", "Incremental filters to apply", "Historical Filter to apply.", *ff.LOCATION_HEADERS]


def _rows(*rows, location=EDGE, header=HEADER):
    """A sheet: the standard header, then [table, incremental, historical] padded, then the location columns."""
    return [header] + [list(row) + [None] * (3 - len(row)) + list(location) for row in rows]


def _filters(folder, run_type, source_type="postgres"):
    return ff.filters(ff.read(source_type, run_type, folder=folder, log=LOG), log=LOG)


def test_read_gives_each_table_its_location_from_the_row(tmp_path):
    _workbook(tmp_path / "edge_bronze_master.xlsx", {"filters": _rows(["units"])})
    row = ff.read("postgres", "historical", folder=tmp_path, log=LOG)["units"]
    assert row.location == ff.Location("Edge", "postgres", "fms", "public", "{env}_EDGE_BRONZE", "STOREDGE_FMS_PUBLIC")
    assert row.statement == "" and row.label == "edge_bronze_master.xlsx row 2 (units)"


def test_read_uses_the_first_sheet_with_the_columns_and_matches_headers_loosely(tmp_path):
    header = ["  TARGET TABLE NAME ", "Incremental Filters to apply.", "x", *[f" {name.upper()} " for name in ff.LOCATION_HEADERS]]
    _workbook(tmp_path / "edge_bronze_master.xlsx", {
        "notes": [["Target Table Name", "Something else"], ["other"]],          # no location columns: not this sheet
        "filters": _rows(["units", "created_at > '2025-01-01'"], [None], ["notes"], header=header),
    })
    rows = ff.read("postgres", "incremental", folder=tmp_path, log=LOG)
    assert list(rows) == ["units", "notes"], "a row with no table name is skipped"
    assert rows["units"].statement == "created_at > '2025-01-01'"
    assert rows["notes"].label.endswith("row 4 (notes)"), "row numbers are the sheet's"


def test_a_workbook_without_the_location_columns_is_an_error_naming_the_file(tmp_path):
    _workbook(tmp_path / "edge_bronze_master.xlsx", {"s": [["Target Table Name", "Incremental filters to apply"], ["units", None]]})
    with pytest.raises(ValueError, match="edge_bronze_master.xlsx has no sheet with the columns"):
        ff.read("postgres", "incremental", folder=tmp_path, log=LOG)


def test_the_layer_picks_its_workbook_and_no_other_file_is_read(tmp_path):
    """bronze_postgres reads edge_bronze_master.xlsx only. A bad row in another file does not stop it."""
    _workbook(tmp_path / "edge_bronze_master.xlsx", {"s": _rows(["units"])})
    _workbook(tmp_path / "sitelink_bronze_master.xlsx", {"s": _rows(["Accounts"], location=SITELINK)})
    _workbook(tmp_path / "notes.xlsx", {"s": _rows(["broken"], location=[None] * 6)})
    assert list(ff.read("postgres", "historical", folder=tmp_path, log=LOG)) == ["units"]
    assert list(ff.read("mssql", "historical", folder=tmp_path, log=LOG)) == ["Accounts"]


def test_a_layer_with_no_workbook_or_a_missing_file_is_an_error(tmp_path):
    with pytest.raises(ValueError, match="bronze_athena has no master workbook"):
        ff.read("athena", "historical", folder=tmp_path, log=LOG)
    with pytest.raises(ValueError, match="for --layer_type bronze_mssql is missing"):
        ff.read("mssql", "historical", folder=tmp_path, log=LOG)


def test_a_target_database_without_env_is_an_error(tmp_path):
    _workbook(tmp_path / "edge_bronze_master.xlsx",
              {"s": _rows(["units"], location=["Edge", "fms", "public", "Snowflake", "DEV_EDGE_BRONZE", "X"])})
    with pytest.raises(ValueError, match=r"row 2 \(units\): target_database 'DEV_EDGE_BRONZE' has no \{env\}"):
        ff.read("postgres", "historical", folder=tmp_path, log=LOG)


def test_table_names_are_matched_without_regard_to_case(tmp_path, caplog):
    _workbook(tmp_path / "edge_bronze_master.xlsx", {"s": _rows(["Addresses"], ["ADDRESSES"])})
    with caplog.at_level(logging.WARNING):
        rows = ff.read("postgres", "historical", folder=tmp_path, log=LOG)
    assert list(rows) == ["Addresses"] and "row 3 (ADDRESSES): the table is listed twice" in caplog.text
    assert ff.spelled(["addresses", "nosuch"], rows) == ["Addresses", "nosuch"]


def test_a_workbook_without_the_run_types_filter_column_still_gives_the_locations(tmp_path, caplog):
    _workbook(tmp_path / "edge_bronze_master.xlsx", {"s": [["Target Table Name", *ff.LOCATION_HEADERS], ["units", *EDGE]]})
    with caplog.at_level(logging.WARNING):
        rows = ff.read("postgres", "historical", folder=tmp_path, log=LOG)
    assert rows["units"].statement == "" and rows["units"].location.source_schema == "public"
    assert "has no 'historical filter to apply' column" in caplog.text


@pytest.mark.parametrize("column", ff.LOCATION_HEADERS[1:])
def test_an_empty_location_cell_is_an_error_naming_the_file_row_and_column(tmp_path, column):
    location = list(EDGE)
    location[ff.LOCATION_HEADERS.index(column)] = None
    _workbook(tmp_path / "edge_bronze_master.xlsx", {"s": _rows(["units"], location=location)})
    with pytest.raises(ValueError, match=rf"edge_bronze_master.xlsx row 2 \(units\): {column} is empty"):
        ff.read("postgres", "historical", folder=tmp_path, log=LOG)


def test_mysql_source_schema_is_optional_and_defaults_to_the_database(tmp_path):
    """MySQL has no schema level: a blank cell, or no source_schema column at all, means the source_database."""
    crm = ["CRM", "dev", None, "Snowflake", "{env}_CRM_BRONZE", "DEV"]
    _workbook(tmp_path / "crm_bronze_master.xlsx", {"s": _rows(["users"], location=crm)})
    assert ff.read("mysql_crm", "historical", folder=tmp_path, log=LOG)["users"].location.source_schema == "dev"
    header = [name for name in HEADER if name != "source_schema"]
    _workbook(tmp_path / "crm_bronze_master.xlsx", {"s": [header, ["users", None, None, "CRM", "dev", "Snowflake", "{env}_CRM_BRONZE", "DEV"]]})
    assert ff.read("mysql_crm", "historical", folder=tmp_path, log=LOG)["users"].location.source_schema == "dev"


def test_a_target_that_is_not_an_engine_is_an_error(tmp_path):
    _workbook(tmp_path / "edge_bronze_master.xlsx", {"s": _rows(["units"], location=["Edge", "fms", "public", "Oracle", "X", "Y"])})
    with pytest.raises(ValueError, match="target_application 'Oracle' is not an engine"):
        ff.read("postgres", "historical", folder=tmp_path, log=LOG)


def test_mysql_crm_compared_against_itself_needs_no_env_and_no_schemas(tmp_path):
    """CRM has no Snowflake target yet: target MySQL CRM, the same database, both schema cells blank."""
    _workbook(tmp_path / "crm_bronze_master.xlsx", {"s": _rows(["users"], location=["CRM", "dev", None, "MySQL CRM", "dev", None])})
    row = ff.read("mysql_crm", "historical", folder=tmp_path, log=LOG)["users"]
    assert row.location == ff.Location("CRM", "mysql_crm", "dev", "dev", "dev", "dev", "mysql_crm")
    src, tgt = ff.convert("select count(*) from users where id > 1", "mysql_crm", "dev", "dev", "dev", "dev", "mysql_crm")
    assert src == tgt == "id > 1"
    src, tgt = ff.convert("select count(*) from users u join leads l on u.lead_id = l.id where l.x = 1",
                          "mysql_crm", "dev", "dev", "dev", "dev", "mysql_crm")
    assert src == tgt == "lead_id IN (SELECT id FROM `dev`.`leads` WHERE x = 1)", tgt


@pytest.mark.parametrize("engine", ["SQL Server", "PostgreSQL"])
def test_an_engine_in_source_database_is_an_error(tmp_path, engine):
    """source_database is the database name (fms), not the engine: an old workbook fails loudly, not with bad SQL."""
    other = ["Marketplace", engine, "app", "Snowflake", "{env}_MARKETPLACE_BRONZE", "APP"]
    _workbook(tmp_path / "edge_bronze_master.xlsx", {"s": [HEADER, ["units", None, None, *EDGE], ["listings", None, None, *other]]})
    with pytest.raises(ValueError, match=rf"row 3 \(listings\): source_database is '{engine}', an engine"):
        ff.read("postgres", "historical", folder=tmp_path, log=LOG)


def test_the_source_is_named_database_schema_table():
    """The base table and the filter's sub-select both carry the source database, as the Snowflake side does."""
    from yaml_generation.yaml_builder import TableSpec, source_ref
    assert source_ref(TableSpec("postgres", "public", "units", "{env}_EDGE_BRONZE", "S", "fms"), ff.RULES["postgres"]) \
        == '"fms"."public"."units"'
    assert source_ref(TableSpec("mssql", "dbo", "Units", "{env}_SITELINK_BRONZE", "S", "DevT5000"), ff.RULES["mssql"]) \
        == "[DevT5000].[dbo].[Units]"
    source, _ = ff.convert("select count(*) from units u\njoin facilities f on u.facility_id = f.id\nwhere f.company_id in (1)",
                           "postgres", "public", "{env}_EDGE_BRONZE", "S", "fms")
    assert 'FROM "fms"."public"."facilities"' in source


def test_the_engine_is_matched_by_label_or_key_in_any_case():
    assert ff.engine_of("PostgreSQL") == ff.engine_of(" postgres ") == "postgres"
    assert ff.engine_of("sql server") == ff.engine_of("MSSQL") == "mssql"
    assert ff.engine_of("Snowflake") == "snowflake"
    assert ff.engine_of("MySQL CRM") == "mysql_crm" and ff.engine_of(None) is None


def test_a_table_listed_twice_keeps_the_first_row_with_a_warning(tmp_path, caplog):
    _workbook(tmp_path / "edge_bronze_master.xlsx", {"s": _rows(["units", "units\nwhere id > 1"], ["units", "units\nwhere id > 2"])})
    with caplog.at_level(logging.WARNING):
        assert _filters(tmp_path, "incremental")["units"] == ("id > 1", "id > 1")
    assert "row 3 (units): the table is listed twice" in caplog.text


def test_a_refused_first_row_is_the_tables_row_so_the_table_is_loaded_in_full(tmp_path):
    """The first row is where the table lives, so its filter is the one used; a later row for the same table is not."""
    _workbook(tmp_path / "edge_bronze_master.xlsx", {"s": _rows(["units", 'units\nwhere "id" > 1'], ["units", "units\nwhere id > 2"])})
    assert "units" not in _filters(tmp_path, "incremental")


# --- filters(): the run type picks the column, each side is written for the row's location ---------------------------

@pytest.fixture
def folder(tmp_path):
    _workbook(tmp_path / "edge_bronze_master.xlsx", {"filters": _rows(
        ["units", "units\nwhere created_at >= '2025-01-01'", "units\nwhere created_at >= '2020-01-01'"],
        ["notes", "", "notes\nwhere body is not null"],
        ["events", "events j\nwhere j.created_at > '2025-06-01' -- note", None],
        ["bad", "bad\nwhere a = 1 or b = 2", None],
    )})
    return tmp_path


def test_incremental_run_reads_the_incremental_column(folder):
    filters = _filters(folder, "incremental")
    assert filters["units"] == ("created_at >= '2025-01-01'", "created_at >= '2025-01-01'")
    assert "notes" not in filters, "an empty cell means no filter"


def test_historical_run_reads_the_historical_column(folder):
    filters = _filters(folder, "historical")
    assert filters["units"][0] == "created_at >= '2020-01-01'"
    assert filters["notes"] == ("body is not null", "body is not null")
    assert "units" in filters and "events" not in filters


def test_the_snowflake_side_is_written_for_the_rows_own_location(tmp_path):
    _workbook(tmp_path / "edge_bronze_master.xlsx", {"s": _rows(["events", None, "events j\njoin facilities f on j.facility_id=f.id\n"
                                                                    "where f.company_id in (137)"])})
    _, target = _filters(tmp_path, "historical")["events"]
    assert target == ('facility_id IN (SELECT id FROM {env}_EDGE_BRONZE."STOREDGE_FMS_PUBLIC"."FACILITIES" '
                      "WHERE company_id in (137) AND _FIVETRAN_ACTIVE = TRUE)")


def test_a_bare_condition_cell_filters_its_own_table_without_a_join(tmp_path):
    _workbook(tmp_path / "edge_bronze_master.xlsx", {"s": _rows(["account_balance_items", None, "created_at >= '2026-08-01'"])})
    assert _filters(tmp_path, "historical") == {
        "account_balance_items": ("created_at >= '2026-08-01'", "created_at >= '2026-08-01'")}


def test_a_refused_row_loads_its_table_in_full_and_is_logged(folder, caplog):
    with caplog.at_level(logging.WARNING):
        filters = _filters(folder, "incremental")
    assert "bad" not in filters
    assert "loaded in full" in caplog.text and "row 5 (bad)" in caplog.text


def test_a_filter_refused_by_check_filter_is_not_applied(folder, caplog):
    with caplog.at_level(logging.WARNING):
        filters = _filters(folder, "incremental")
    assert "events" not in filters
    assert "loaded in full" in caplog.text


def test_a_cell_about_another_table_is_refused_and_the_table_loaded_in_full(tmp_path, caplog):
    # A cell copied from another row would filter the wrong table. The row's table must be the one the statement reads.
    _workbook(tmp_path / "edge_bronze_master.xlsx", {"s": _rows(
        ["discount_plan_ledger_instances", "discount_plan_ledger_instances_line_items j\n"
                                           "join discount_plan_ledger_instances ss on j.discount_plan_ledger_instance_id=ss.id\n"
                                           "where j.created_at >= '2026-08-01'"])})
    with caplog.at_level(logging.WARNING):
        filters = _filters(tmp_path, "incremental")
    assert "discount_plan_ledger_instances" not in filters
    assert "is about 'discount_plan_ledger_instances_line_items', not 'discount_plan_ledger_instances'" in caplog.text


MS_ARGS = ("mssql", "dbo", "DEV_SITELINK_BRONZE", "SITELINK")


def test_sql_server_joins_are_written_with_brackets():
    src, _ = ff.convert("select count(*) from AcctTypes j join Promotions p on j.promo_id=p.id where p.active = 1", *MS_ARGS)
    assert src == "promo_id IN (SELECT id FROM [dbo].[Promotions] WHERE active = 1)"


def test_sitelink_joins_on_its_own_keys_through_sites_to_owners():
    src, _ = ff.convert("select count(*) from Units t join Sites s on t.SiteID = s.SiteID join Owners o on s.OwnerID = o.OwnerID "
                        "where o.sCorpCode in ('SLQA', 'CVIJAY')", *MS_ARGS)
    assert src == ("SiteID IN (SELECT SiteID FROM [dbo].[Sites] WHERE OwnerID IN "
                   "(SELECT OwnerID FROM [dbo].[Owners] WHERE sCorpCode in ('SLQA', 'CVIJAY')))")


def test_a_bind_parameter_is_refused_because_it_has_no_value():
    with pytest.raises(ff.Refused, match="bind parameter"):
        ff.convert("select count(*) from AcctTypes t where t.scorpcode = :scorpcode", *MS_ARGS)


def test_a_postgresql_cast_is_not_mistaken_for_a_bind_parameter():
    source_where, _ = ff.convert("select count(*) from units where created_at::date > '2024-08-01'", "postgres", "public",
                                 "DEV_EDGE_BRONZE", "STOREDGE_FMS_PUBLIC")
    assert "::date" in source_where


def test_sql_server_reads_only_its_own_workbook_and_compare_full_is_no_filter(tmp_path, caplog):
    # The PostgreSQL workbook in the same folder must not be applied to SQL Server.
    _workbook(tmp_path / "edge_bronze_master.xlsx", {"filters": _rows(["units", "units\nwhere id > 1"])})
    _workbook(tmp_path / "sitelink_bronze_master.xlsx", {"Sheet1": _rows(
        ["AcctTypes", "AcctTypes t\nwhere t.scorpcode = :scorpcode"],
        ["Promotions", "compare full"],
        ["Accounts", "Accounts t\nwhere t.active = 1"], location=SITELINK)})
    with caplog.at_level(logging.WARNING):
        filters = _filters(tmp_path, "incremental", source_type="mssql")
    assert filters == {"Accounts": ("active = 1", "active = 1")}
    assert "AcctTypes" not in filters and "loaded in full" in caplog.text


def test_quoted_identifiers_are_refused_as_the_docstring_says():
    with pytest.raises(ff.Refused, match="quoted identifiers"):
        ff.convert('select count(*) from events j join facilities f on j.facility_id=f.id where f."company_id" in (137)', *ARGS)


# --- the shipped workbooks ----------------------------------------------------------------------------------------------

@pytest.mark.parametrize("run_type", ["historical", "incremental"])
def test_the_shipped_workbooks_give_every_table_a_location(run_type):
    """Read-only smoke test on the real folder: each application's workbook lists only its own engine's tables."""
    edge = ff.read("postgres", run_type, log=LOG)
    sitelink = ff.read("mssql", run_type, log=LOG)
    assert "events" in edge and "NationalMasterAccounts" not in edge
    assert "NationalMasterAccounts" in sitelink and "events" not in sitelink
    assert {row.location.application for row in edge.values()} == {"Edge"}
    assert {row.location.application for row in sitelink.values()} == {"SiteLink"}
    assert isinstance(ff.filters(edge, log=LOG), dict)


# --- the type mapping sheet: schema_mapping() gives the schema YAML its datatype_map --------------------------------

MAPPING_HEADER = ["source_data_type", "target_data_type", "precision_check", "notes"]


def _mapping_book(folder, sheet="MSSQL schema mapping", rows=(), name="sitelink_bronze_master.xlsx"):
    sheets = {"Sheet1": _rows(["Units"], location=SITELINK)}
    if sheet:
        sheets[sheet] = [MAPPING_HEADER, *rows]
    return _workbook(folder / name, sheets)


def test_schema_mapping_reads_the_types_and_the_precision_checks(tmp_path):
    _mapping_book(tmp_path, rows=[["int", "NUMBER", "numeric", ""], ["nvarchar", "text", "Character", None],
                                  ["bit", "boolean", None, "no check"], [None, None, None, None]])
    datatype_map, checks = ff.schema_mapping("mssql", folder=tmp_path, log=LOG)
    assert datatype_map == {"int": "number", "nvarchar": "text", "bit": "boolean"}   # Snowflake type in lower case
    assert checks == {"numeric": ["int"], "character": ["nvarchar"]}


def test_the_source_type_keeps_its_spelling_for_the_validators_exact_lookup(tmp_path):
    _mapping_book(tmp_path, sheet="Postgres schema mapping", name="edge_bronze_master.xlsx",
                  rows=[["USER-DEFINED", "variant"], ["character varying", "text"]])
    assert list(ff.schema_mapping("postgres", folder=tmp_path, log=LOG)[0]) == ["USER-DEFINED", "character varying"]


def test_the_mapping_sheet_name_is_matched_without_regard_to_case(tmp_path):
    _mapping_book(tmp_path, sheet="mssql SCHEMA mapping", rows=[["int", "number"]])
    assert ff.schema_mapping("mssql", folder=tmp_path, log=LOG)[0] == {"int": "number"}


def test_a_missing_mapping_sheet_stops_the_run(tmp_path):
    _mapping_book(tmp_path, sheet=None)
    with pytest.raises(ValueError, match="has no sheet 'MSSQL schema mapping'"):
        ff.schema_mapping("mssql", folder=tmp_path, log=LOG)


@pytest.mark.parametrize("row", [["int", None], [None, "number"]])
def test_an_empty_type_cell_stops_the_run_and_names_the_row(tmp_path, row):
    _mapping_book(tmp_path, rows=[["bit", "boolean"], row])
    with pytest.raises(ValueError, match="row 3: source_data_type and target_data_type must both be filled"):
        ff.schema_mapping("mssql", folder=tmp_path, log=LOG)


def test_an_unknown_precision_check_stops_the_run(tmp_path):
    _mapping_book(tmp_path, rows=[["int", "number", "length"]])
    with pytest.raises(ValueError, match="precision_check is 'length'"):
        ff.schema_mapping("mssql", folder=tmp_path, log=LOG)


def test_a_type_listed_twice_keeps_its_first_row_with_a_warning(tmp_path, caplog):
    _mapping_book(tmp_path, rows=[["int", "number"], ["INT", "float"]])
    with caplog.at_level(logging.WARNING):
        assert ff.schema_mapping("mssql", folder=tmp_path, log=LOG)[0] == {"int": "number"}
    assert "row 3: type INT is listed twice" in caplog.text


def test_a_mapping_sheet_with_no_row_stops_the_run(tmp_path):
    _mapping_book(tmp_path)
    with pytest.raises(ValueError, match="no type mapping row"):
        ff.schema_mapping("mssql", folder=tmp_path, log=LOG)


def test_the_shipped_workbooks_have_a_type_mapping_sheet():
    """Seeded from the old hand-written datatype_map. config/ is not in git, so the check is on the workbooks only."""
    postgres, _ = ff.schema_mapping("postgres", log=LOG)
    mssql, _ = ff.schema_mapping("mssql", log=LOG)
    assert (len(postgres), postgres["USER-DEFINED"], postgres["timestamp without time zone"]) == (16, "variant", "timestamp_ntz")
    assert (len(mssql), mssql["nvarchar"], mssql["bit"]) == (9, "text", "boolean")
