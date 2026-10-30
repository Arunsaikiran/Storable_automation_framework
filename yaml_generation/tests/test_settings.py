"""Unit tests for settings.py: the global exclusions, the table rules workbook and the rules file picker."""

import pytest
from openpyxl import Workbook

from yaml_generation import settings


def _exclusions(tmp_path, text):
    path = tmp_path / "exclusions.yaml"
    path.write_text(text, encoding="utf-8")
    return path


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


# --- global exclusions -----------------------------------------------------------------------------------------

def test_a_missing_file_means_no_global_exclusions(tmp_path):
    assert settings.load_global_exclusions(tmp_path / "absent.yaml") == set()


def test_global_exclusion_names_are_normalised_like_the_columns(tmp_path):
    path = _exclusions(tmp_path, "exclude_columns:\n  - Fivetran Synced\n  - Created-By\n")
    assert settings.load_global_exclusions(path) == {"fivetran_synced", "created_by"}


def test_the_shipped_exclusions_file_lists_the_fivetran_columns():
    assert settings.load_global_exclusions() >= {"_fivetran_synced", "_fivetran_deleted", "_fivetran_id",
                                                 "_fivetran_index", "_fivetran_active", "_fivetran_start", "_fivetran_end"}


@pytest.mark.xfail(strict=True, reason="ISSUE-05: a misspelt top-level key in exclusions.yaml is ignored without a message")
def test_a_misspelt_key_in_the_global_file_is_refused(tmp_path):
    path = _exclusions(tmp_path, "exclude_column:\n  - _fivetran_synced\n")
    with pytest.raises(ValueError, match="exclude_column"):
        settings.load_global_exclusions(path)


# --- table rules workbook --------------------------------------------------------------------------------------

def test_incremental_column_is_read_from_the_rules_workbook(tmp_path):
    path = _rules(tmp_path, [["units", None, None, None, None, "Created At"]])
    assert settings.load_table_rules(path, "postgres")["units"].incremental_column == "Created At"


def test_a_rules_workbook_without_the_incremental_column_still_loads(tmp_path):
    old_headers = ["table_name", "key_columns", "exclude_columns", "source_where", "target_where"]
    path = _rules(tmp_path, [["units", "id", None, None, None]], headers=old_headers)
    assert settings.load_table_rules(path, "postgres")["units"].incremental_column is None


def test_table_rule_reads_every_column(tmp_path):
    path = _rules(tmp_path, [["units", "Company_ID, site id", "notes, last-modified-by", "status <> 'x'", "STATUS <> 'x'"]])
    rule = settings.load_table_rules(path, "postgres")["units"]
    assert rule.key_columns == ["company_id", "site_id"]
    assert rule.exclude == {"notes", "last_modified_by"}
    assert rule.source_where == "status <> 'x'"
    assert rule.target_where == "STATUS <> 'x'"


def test_blank_cells_mean_no_rule(tmp_path):
    path = _rules(tmp_path, [["units", None, None, None, None]])
    rule = settings.load_table_rules(path, "postgres")["units"]
    assert rule.key_columns is None and rule.exclude == set()
    assert rule.source_where is None and rule.target_where is None


def test_row_without_a_table_name_is_skipped(tmp_path):
    path = _rules(tmp_path, [[None, "id", None, None, None], ["units", None, None, None, None]])
    assert list(settings.load_table_rules(path, "postgres")) == ["units"]


def test_a_table_listed_twice_is_refused_with_its_row(tmp_path):
    path = _rules(tmp_path, [["units", None, None, None, None], ["units", None, None, None, None]])
    with pytest.raises(ValueError, match="row 3.*listed twice"):
        settings.load_table_rules(path, "postgres")


def test_unknown_header_is_refused(tmp_path):
    path = _rules(tmp_path, [], headers=["table_name", "exclude"])
    with pytest.raises(ValueError, match="unknown"):
        settings.load_table_rules(path, "postgres")


def test_sql_server_source_filter_with_true_is_refused_with_its_row(tmp_path):
    path = _rules(tmp_path, [["units", None, None, "is_active = true", None]])
    with pytest.raises(ValueError, match="row 2 \\(units\\) source_where"):
        settings.load_table_rules(path, "mssql")


def test_the_same_filter_with_true_is_allowed_on_postgres(tmp_path):
    path = _rules(tmp_path, [["units", None, None, "is_active = true", None]])
    assert settings.load_table_rules(path, "postgres")["units"].source_where == "is_active = true"


def test_a_source_filter_with_the_env_placeholder_is_refused(tmp_path):
    path = _rules(tmp_path, [["units", None, None, "id IN (SELECT id FROM {env}_EDGE_BRONZE.EDGE.NOTES)", None]])
    with pytest.raises(ValueError, match="may not contain"):
        settings.load_table_rules(path, "postgres")


def test_a_filter_with_a_forbidden_character_is_refused(tmp_path):
    path = _rules(tmp_path, [["units", None, None, "a = 1; drop table x", None]])
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


def test_reconciliation_report_has_the_documented_columns(tmp_path):
    path = settings.write_reconciliation(tmp_path / "out" / "r.csv", "t", [("units", "id", "ID", "MATCHED", "")])
    assert path.read_text(encoding="utf-8").splitlines()[0] == "run_at,table,source_column,target_column,status,note"
