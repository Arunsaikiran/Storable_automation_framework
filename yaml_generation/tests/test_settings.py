"""Unit tests for settings.py: the common exclusions, the table rules workbook and the rules file picker."""

import pytest
from openpyxl import Workbook

from yaml_generation import settings


def _exclusions(tmp_path, text):
    path = tmp_path / "exclusions.yaml"
    path.write_text(text, encoding="utf-8")
    return path


def _row(table, source_type="postgres", source_schema=None, key=None, exclude=None, source_where=None, target_where=None,
         incremental=None):
    """One rules row, in the order of settings.RULE_HEADERS."""
    return [source_type, source_schema, table, key, exclude, source_where, target_where, incremental]


def _rules(tmp_path, rows, sheet="table_rules", headers=settings.RULE_HEADERS, name="rules.xlsx"):
    path = tmp_path / name
    book = Workbook()
    ws = book.active
    ws.title = sheet
    ws.append(headers)
    for row in rows:
        ws.append(row)
    book.save(path)
    return path


# --- common exclusions -----------------------------------------------------------------------------------------

def test_a_missing_file_means_no_exclusions(tmp_path):
    assert settings.load_exclusions(tmp_path / "absent.yaml", source_type="postgres") == set()


def test_exclusion_names_are_normalised_like_the_columns(tmp_path):
    path = _exclusions(tmp_path, "snowflake:\n  exclude_columns:\n    - Fivetran Synced\n    - Created-By\n")
    assert settings.load_exclusions(path, source_type="postgres") == {"fivetran_synced", "created_by"}


def test_a_source_section_applies_to_a_run_of_that_source_only(tmp_path):
    path = _exclusions(tmp_path, "postgres:\n  exclude_columns:\n    - legacy_code\nmssql:\n  exclude_columns:\n    - uts\n")
    assert settings.load_exclusions(path, source_type="postgres") == {"legacy_code"}
    assert settings.load_exclusions(path, source_type="mssql") == {"uts"}


def test_the_snowflake_section_applies_to_every_run(tmp_path):
    path = _exclusions(tmp_path, "snowflake:\n  exclude_columns:\n    - shared_col\n")
    assert settings.load_exclusions(path, source_type="postgres") == {"shared_col"}
    assert settings.load_exclusions(path, source_type="mssql") == {"shared_col"}


def test_the_shipped_file_has_every_source_and_the_snowflake_fivetran_columns():
    fivetran = {"_fivetran_synced", "_fivetran_deleted", "_fivetran_id", "_fivetran_index", "_fivetran_active",
                "_fivetran_start", "_fivetran_end"}
    for source_type in ("postgres", "mssql", "athena", "redshift"):
        assert settings.load_exclusions(source_type=source_type) >= fivetran
    assert "uts" in settings.load_exclusions(source_type="mssql")
    assert "uts" not in settings.load_exclusions(source_type="postgres")


def test_a_section_for_another_source_does_not_reach_a_run(tmp_path):
    path = _exclusions(tmp_path, "mssql:\n  exclude_columns:\n    - uts\n")
    assert "uts" not in settings.load_exclusions(path, source_type="postgres")


def test_a_common_section_is_refused_because_every_section_is_a_data_source(tmp_path):
    path = _exclusions(tmp_path, "common:\n  exclude_columns:\n    - _fivetran_synced\n")
    with pytest.raises(ValueError, match="unknown section.*common"):
        settings.load_exclusions(path, source_type="postgres")


def test_a_misspelt_section_is_refused_not_ignored(tmp_path):
    path = _exclusions(tmp_path, "snowflak:\n  exclude_columns:\n    - _fivetran_synced\n")
    with pytest.raises(ValueError, match="snowflak"):
        settings.load_exclusions(path, source_type="postgres")


# --- table rules workbook --------------------------------------------------------------------------------------

def test_incremental_column_is_read_from_the_rules_workbook(tmp_path):
    path = _rules(tmp_path, [_row("units", incremental="Created At")])
    assert settings.load_table_rules(path, "postgres")["units"].incremental_column == "Created At"


def test_a_rules_workbook_without_source_type_is_refused(tmp_path):
    old_headers = ["table_name", "key_columns", "exclude_columns", "source_where", "target_where"]
    path = _rules(tmp_path, [["units", "id", None, None, None]], headers=old_headers)
    with pytest.raises(ValueError, match="missing: \\['source_type'\\]"):
        settings.load_table_rules(path, "postgres")


def test_table_rule_reads_every_column(tmp_path):
    path = _rules(tmp_path, [_row("units", key="Company_ID, site id", exclude="notes, last-modified-by",
                                  source_where="status <> 'x'", target_where="STATUS <> 'x'")])
    rule = settings.load_table_rules(path, "postgres")["units"]
    assert rule.key_columns == ["company_id", "site_id"]
    assert rule.exclude == {"notes", "last_modified_by"}
    assert rule.source_where == "status <> 'x'"
    assert rule.target_where == "STATUS <> 'x'"


def test_blank_cells_mean_no_rule(tmp_path):
    path = _rules(tmp_path, [_row("units")])
    rule = settings.load_table_rules(path, "postgres")["units"]
    assert rule.key_columns is None and rule.exclude == set()
    assert rule.source_where is None and rule.target_where is None


def test_row_without_a_table_name_is_skipped(tmp_path):
    path = _rules(tmp_path, [_row(None, key="id"), _row("units")])
    assert list(settings.load_table_rules(path, "postgres")) == ["units"]


def test_a_table_listed_twice_for_the_same_run_is_refused_with_its_row(tmp_path):
    path = _rules(tmp_path, [_row("units"), _row("units")])
    with pytest.raises(ValueError, match="row 3.*listed twice"):
        settings.load_table_rules(path, "postgres")


def test_a_row_without_source_type_is_refused_with_its_row(tmp_path):
    path = _rules(tmp_path, [_row("units", source_type=None)])
    with pytest.raises(ValueError, match="row 2 \\(units\\): source_type must be one of"):
        settings.load_table_rules(path, "postgres")


def test_an_unknown_source_type_is_refused(tmp_path):
    path = _rules(tmp_path, [_row("units", source_type="mysql")])
    with pytest.raises(ValueError, match="found 'mysql'"):
        settings.load_table_rules(path, "postgres")


def test_a_rule_applies_only_to_its_own_database(tmp_path):
    """The same table name in two databases: each run reads only its own row."""
    path = _rules(tmp_path, [_row("units", exclude="legacy_code"), _row("units", source_type="mssql", source_schema="dbo",
                                                                        exclude="uts")])
    assert settings.load_table_rules(path, "postgres")["units"].exclude == {"legacy_code"}
    assert settings.load_table_rules(path, "mssql", "dbo")["units"].exclude == {"uts"}


def test_a_rule_applies_only_to_its_own_schema(tmp_path):
    path = _rules(tmp_path, [_row("units", source_schema="sales", exclude="x")])
    assert settings.load_table_rules(path, "postgres", "public") == {}
    assert settings.load_table_rules(path, "postgres", "sales")["units"].exclude == {"x"}


def test_a_blank_schema_applies_to_any_schema_of_the_database(tmp_path):
    path = _rules(tmp_path, [_row("units", exclude="x")])
    assert settings.load_table_rules(path, "postgres", "anything")["units"].exclude == {"x"}


def test_sql_server_source_filter_with_true_is_refused_with_its_row(tmp_path):
    path = _rules(tmp_path, [_row("units", source_type="mssql", source_where="is_active = true")])
    with pytest.raises(ValueError, match="row 2 \\(units\\) source_where"):
        settings.load_table_rules(path, "mssql")


def test_the_same_filter_with_true_is_allowed_on_postgres(tmp_path):
    path = _rules(tmp_path, [_row("units", source_where="is_active = true")])
    assert settings.load_table_rules(path, "postgres")["units"].source_where == "is_active = true"


def test_a_source_filter_with_the_env_placeholder_is_refused(tmp_path):
    path = _rules(tmp_path, [_row("units", source_where="id IN (SELECT id FROM {env}_EDGE_BRONZE.EDGE.NOTES)")])
    with pytest.raises(ValueError, match="may not contain"):
        settings.load_table_rules(path, "postgres")


def test_a_filter_with_a_forbidden_character_is_refused(tmp_path):
    path = _rules(tmp_path, [_row("units", source_where="a = 1; drop table x")])
    with pytest.raises(ValueError, match="may not contain"):
        settings.load_table_rules(path, "postgres")


def test_an_empty_workbook_gives_no_rules(tmp_path):
    book = Workbook()
    path = tmp_path / "empty.xlsx"
    book.save(path)
    assert settings.load_table_rules(path, "postgres") == {}


# --- choosing the rules file -----------------------------------------------------------------------------------

def test_explicit_rules_file_must_exist(tmp_path):
    with pytest.raises(FileNotFoundError):
        settings.pick_rules_file(tmp_path / "missing.xlsx")


def test_no_workbook_in_the_folder_means_no_rules(tmp_path):
    assert settings.pick_rules_file(folder=tmp_path) is None


def test_one_workbook_is_used_directly(tmp_path):
    only = _rules(tmp_path, [])
    assert settings.pick_rules_file(folder=tmp_path) == only


def test_owner_lock_files_are_not_rules_files(tmp_path):
    (tmp_path / "~$dev_rules.xlsx").write_text("lock", encoding="utf-8")
    assert settings.pick_rules_file(folder=tmp_path) is None


def test_several_workbooks_without_a_terminal_are_an_error(tmp_path):
    _rules(tmp_path, [], name="a.xlsx")
    _rules(tmp_path, [], name="b.xlsx")
    with pytest.raises(ValueError, match="keep only the table rules workbook there"):
        settings.pick_rules_file(folder=tmp_path, interactive=False)


def test_several_workbooks_at_a_terminal_are_chosen_by_number(tmp_path):
    _rules(tmp_path, [], name="a.xlsx")
    second = _rules(tmp_path, [], name="b.xlsx")
    assert settings.pick_rules_file(folder=tmp_path, interactive=True, ask=lambda _: "2") == second


@pytest.mark.parametrize("answer", ["", "9", "x"])
def test_blank_or_wrong_answer_at_the_terminal(tmp_path, answer):
    _rules(tmp_path, [], name="a.xlsx")
    _rules(tmp_path, [], name="b.xlsx")
    if answer == "":
        assert settings.pick_rules_file(folder=tmp_path, interactive=True, ask=lambda _: answer) is None
    else:
        with pytest.raises(ValueError, match="not a number"):
            settings.pick_rules_file(folder=tmp_path, interactive=True, ask=lambda _: answer)
