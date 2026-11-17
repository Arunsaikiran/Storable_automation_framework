"""A Fivetran-landed Snowflake source (Newbook): key from SHOW PRIMARY KEYS, without the excluded Fivetran columns, and
active rows only on the source side (17/11/2026)."""

import pandas as pd
import yaml

from yaml_generation import db_rules
from yaml_generation.tests.helpers import FakeDB, GLOBAL_EXCLUDED, LOG, STAMP, make_args
from yaml_generation import generate_yaml

SCHEMA = "NB_DEV_AU_SQL01_NEWBOOK_COMMON"
COLUMNS = [("ID", "NUMBER"), ("NAME", "TEXT"), ("_FIVETRAN_START", "TIMESTAMP_TZ"), ("_FIVETRAN_ACTIVE", "BOOLEAN")]


class SnowflakeSource(FakeDB):
    """FakeDB that also answers SHOW PRIMARY KEYS the way Snowflake does: lower-case headers, one row per key column."""

    def execute_query(self, sql):
        if sql.startswith("SHOW PRIMARY KEYS"):
            self.queries.append(sql)
            return pd.DataFrame([("DENOMINATIONS", "_FIVETRAN_START", 2), ("DENOMINATIONS", "ID", 1), ("FILES_LINK", "ID", 1)],
                                columns=["table_name", "column_name", "key_sequence"])
        return super().execute_query(sql)


def test_keys_come_from_one_show_per_schema_in_key_order():
    db_rules._SCHEMA_KEYS.clear()
    db = SnowflakeSource({})
    rules = db_rules.RULES["newbook_common"]
    assert db_rules.primary_key_columns(db, SCHEMA, "denominations", rules, prefix="DEV_NEWBOOK_AU_BRONZE.") == ["ID", "_FIVETRAN_START"]
    assert db_rules.primary_key_columns(db, SCHEMA, "FILES_LINK", rules, prefix="DEV_NEWBOOK_AU_BRONZE.") == ["ID"]
    assert db.queries == [f"SHOW PRIMARY KEYS IN SCHEMA DEV_NEWBOOK_AU_BRONZE.{SCHEMA}"]


def test_newbook_data_yaml_keys_on_id_and_reads_active_rows_on_both_sides(tmp_path):
    db_rules._SCHEMA_KEYS.clear()
    tables = {(SCHEMA, "DENOMINATIONS"): COLUMNS}
    args = make_args(tables=["DENOMINATIONS"], source_type="newbook_common", source_schema=SCHEMA,
                     source_database="{env}_NEWBOOK_AU_BRONZE", target_template="{env}_NEWBOOK_AU_BRONZE", target_schema=SCHEMA)
    config = tmp_path / "config"
    results = generate_yaml.generate(args, SnowflakeSource(tables), FakeDB(tables), config,
                                     GLOBAL_EXCLUDED | {"_fivetran_start"}, {}, LOG, STAMP)
    assert ("DENOMINATIONS", "data_validation", "NEW") in [r[:3] for r in results], results
    data = yaml.safe_load((config / "data_validation" / "DENOMINATIONS.yaml").read_text())
    block = data["tables"]["DENOMINATIONS"]["validations"]["data_validation"]
    assert block["sourcecolumn"] == "id_normalized"
    assert "_FIVETRAN_ACTIVE = TRUE" in block["sourcequery"] and "_FIVETRAN_ACTIVE = TRUE" in block["targetquery"]


def test_other_sources_get_no_source_active_filter():
    assert not db_rules.RULES["postgres"].get("fivetran_source")
    assert not db_rules.RULES["mssql"].get("fivetran_source")
    assert not db_rules.RULES["mysql_crm"].get("fivetran_source")
