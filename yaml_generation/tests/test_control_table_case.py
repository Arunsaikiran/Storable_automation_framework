"""The control table matches a table name without regard to case, on both sides (17/11/2026)."""

import pandas as pd
from openpyxl import Workbook, load_workbook

from utils.utility import update_control_table

HEADERS = ["Target Table Name", "count_validation_last_run_date", "layer"]


def test_csv_row_written_with_capitals_is_matched(tmp_path):
    path = tmp_path / "control.csv"
    pd.DataFrame([["CenterShiftPromos", "", "bronze_mysql_crm"]], columns=HEADERS).to_csv(path, index=False)
    update_control_table(str(path), "centershiftpromos", "bronze_mysql_crm", "count", "2026-11-17")
    assert pd.read_csv(path, dtype=str)["count_validation_last_run_date"][0] == "2026-11-17"


def test_workbook_row_written_with_capitals_is_matched(tmp_path):
    path = tmp_path / "bronze_mysql_crm_master.xlsx"
    wb = Workbook()
    wb.active.title = "Sheet1"
    wb.active.append(HEADERS)
    wb.active.append(["BOOKINGS", None, "bronze_mysql_crm"])
    wb.save(path)
    update_control_table(str(path), "Bookings", "bronze_mysql_crm", "count", "2026-11-17")
    assert load_workbook(path)["Sheet1"]["B2"].value == "2026-11-17"
