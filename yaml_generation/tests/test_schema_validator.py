"""Tests for the validators/schema_validation.py changes the generated schema YAML relies on (V1-V4, V6).

Each test writes a small schema YAML, answers the validator's two catalogue queries from a fake, and reads the report.
"""

import pandas as pd
import pytest
import yaml
from openpyxl import Workbook

from utils.utility import data_context
from validators import schema_validation as sv
from yaml_generation import settings

COLUMNS = ["table_name", "column_name", "data_type", "character_maximum_length", "character_octet_length",
           "numeric_precision", "numeric_precision_radix", "numeric_scale", "datetime_precision"]


def col(name, dtype, length=None, precision=None, scale=None, dt_precision=None, table="units"):
    return (table, name, dtype, length, None, precision, None, scale, dt_precision)


class Fake:
    def __init__(self, rows, upper=False):
        self.rows, self.upper, self.queries = rows, upper, []

    def execute_query(self, sql):
        self.queries.append(sql)
        return pd.DataFrame(self.rows, columns=[c.upper() for c in COLUMNS] if self.upper else COLUMNS)


def run(tmp_path, monkeypatch, source_rows, target_rows, environment="dev", source="mssql", rules=None, **config):
    """Runs validate_schema on one table. Returns (failures, system_error, report sheets, target queries)."""
    block = {"tables": {"units": {"source_column_exclusion": [], "target_column_exclusion": []}},
             "source": source, "sourcequery": "SELECT '{table_name}'", "target": "snowflake",
             "targetquery": "SELECT * FROM {env}_SITELINK_BRONZE.information_schema.columns WHERE t = '{table_name}'",
             "datatype_map": {"int": "number", "nvarchar": "text", "datetime": "timestamp_ntz"}, **config}
    path = tmp_path / "schema_validation.yaml"
    path.write_text(yaml.safe_dump({"validations": {"schema_validation": block}}), encoding="utf-8")
    fakes = {source: Fake(source_rows), "snowflake": Fake(target_rows, upper=True)}
    monkeypatch.setattr(sv, "get_database", lambda db_type, *a: fakes[db_type])
    # The real exclusions.yaml (mssql: uts; snowflake: the Fivetran columns). rules: table rule rows, or no rules workbook.
    rules_path = None
    if rules:
        rules_path = tmp_path / "rules.xlsx"
        book = Workbook()
        book.active.title = settings.RULES_SHEET
        for row in [settings.RULE_HEADERS, *rules]:
            book.active.append(row)
        book.save(rules_path)
    monkeypatch.setattr(settings, "pick_rules_file", lambda *a, **k: rules_path)
    out = tmp_path / "out"
    out.mkdir()
    ctx = data_context()(run_id="r", run_at="now", validation_dirs=["schema_validation"], layer_type=[f"bronze_{source}"],
                         outputpaths={"schema_validation": str(out)}, configpaths={"schema_validation": [str(path)]},
                         BASE_DIR=str(tmp_path), environment=environment, tables=["units"])
    failures, system_error = sv.validate_schema(ctx)
    report = pd.read_excel(out / "schema_validation_result_summary.xlsx", sheet_name=None)
    return failures, system_error, report, fakes["snowflake"].queries


@pytest.mark.parametrize("environment, database", [("dev", "DEV"), ("stg", "STG"), ("qat", "QAT"), ("prod", "PRD"),
                                                   ("local", "DEV")])
def test_v1_env_in_the_target_query_is_filled_for_the_run(tmp_path, monkeypatch, environment, database):
    rows = [col("UnitID", "int", precision=10, scale=0)]
    *_, queries = run(tmp_path, monkeypatch, rows, [col("UNITID", "NUMBER", precision=38, scale=0, table="UNITS")],
                      environment=environment)
    assert queries == [f"SELECT * FROM {database}_SITELINK_BRONZE.information_schema.columns WHERE t = 'UNITS'"]


def test_v3_an_unmapped_type_fails_the_column_not_the_whole_table(tmp_path, monkeypatch):
    failures, system_error, report, _ = run(
        tmp_path, monkeypatch, [col("UnitID", "int", precision=10, scale=0), col("Flag", "bit")],
        [col("UNITID", "NUMBER", precision=38, scale=0, table="UNITS"), col("FLAG", "BOOLEAN", table="UNITS")])
    assert (failures, system_error) == (1, False)
    summary = report["Sheet1"] if "Sheet1" in report else next(iter(report.values()))
    assert summary.loc[0, "datatype_validation"] == "FAIL" and pd.isna(summary.loc[0, "error_message"])
    assert report["datatype_validation"].loc[0, "description"] == (
        "no mapping for bit: add it to the filter workbook's schema mapping sheet")


def test_type_mapping_ignores_case_on_both_sides(tmp_path, monkeypatch):
    """The sheet may write INT -> NUMBER or int -> number; the catalogue may give INT or int. All of them match."""
    failures, system_error, _, _ = run(
        tmp_path, monkeypatch, [col("UnitID", "INT", precision=10, scale=0), col("Name", "NVarChar", length=50)],
        [col("UNITID", "NUMBER", precision=38, scale=0, table="UNITS"), col("NAME", "TEXT", length=50, table="UNITS")],
        datatype_map={"Int": "NUMBER", "NVARCHAR": "Text"}, precision_checks={"numeric": ["INT"], "character": ["nvarchar"]})
    assert (failures, system_error) == (0, False)


def test_v2_sql_server_precision_and_length_are_checked_from_precision_checks(tmp_path, monkeypatch):
    checks = {"datetime": ["datetime"], "numeric": ["int"], "character": ["nvarchar"]}
    source = [col("UnitID", "int", precision=10, scale=0), col("sName", "nvarchar", length=100),
              col("dCreated", "datetime", dt_precision=3)]
    target = [col("UNITID", "NUMBER", precision=38, scale=0, table="UNITS"), col("SNAME", "TEXT", length=50, table="UNITS"),
              col("DCREATED", "TIMESTAMP_NTZ", dt_precision=9, table="UNITS")]
    failures, _, report, _ = run(tmp_path, monkeypatch, source, target, precision_checks=checks)
    assert failures == 1
    character = report["character_precision_validation"]
    assert list(character["column_name_source"]) == ["sname"] and list(character["status"]) == ["FAIL"]
    assert list(report["integer_precision_validation"]["status"]) == ["PASS"]      # 10 -> 38: a warning, not a failure
    assert list(report["datetime_precision_validation"]["status"]) == ["PASS"]


def test_v2_without_precision_checks_only_the_old_postgres_types_are_checked(tmp_path, monkeypatch):
    source = [col("sName", "nvarchar", length=100)]
    failures, _, report, _ = run(tmp_path, monkeypatch, source, [col("SNAME", "TEXT", length=50, table="UNITS")])
    assert failures == 0 and "character_precision_validation" not in report


def test_v4_a_smaller_target_precision_fails_even_when_the_scale_fits(tmp_path, monkeypatch):
    failures, _, report, _ = run(
        tmp_path, monkeypatch, [col("Amount", "int", precision=19, scale=0)],
        [col("AMOUNT", "NUMBER", precision=10, scale=0, table="UNITS")], precision_checks={"numeric": ["int"]})
    assert failures == 1
    assert list(report["integer_precision_validation"]["status"]) == ["FAIL"]


def test_v6_no_debug_print_of_character_rows(tmp_path, monkeypatch, capsys):
    run(tmp_path, monkeypatch, [col("sName", "nvarchar", length=10)], [col("SNAME", "TEXT", length=10, table="UNITS")],
        precision_checks={"character": ["nvarchar"]})
    assert "column_name_source" not in capsys.readouterr().out


def test_exclusions_come_from_the_settings_in_any_case_on_both_sides(tmp_path, monkeypatch):
    """uTS (exclusions.yaml, mssql), _FIVETRAN_DELETED (snowflake section) and sNote (rules workbook, this table only)
    are left out; nothing is kept in the YAML. Without the settings each would be a missing column or an unmapped type."""
    source = [col("UnitID", "int", precision=10, scale=0), col("uTS", "timestamp"), col("sNote", "ntext")]
    target = [col("UNITID", "NUMBER", precision=38, scale=0, table="UNITS"), col("UTS", "BINARY", table="UNITS"),
              col("_FIVETRAN_DELETED", "BOOLEAN", table="UNITS")]
    rule = ["mssql", None, "units", None, "snote", None, None, None]
    failures, system_error, report, _ = run(tmp_path, monkeypatch, source, target, rules=[rule])
    assert (failures, system_error) == (0, False)
    assert set(report) == {next(iter(report))}, "no failure sheet: nothing missing, no type mismatch"
