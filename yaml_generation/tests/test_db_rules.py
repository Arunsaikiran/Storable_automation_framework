"""Unit tests for db_rules.py: type names, type kinds, quoting, and the catalogue reads (against FakeDB)."""

import pytest

from helpers import FakeDB, PRIMARY_KEYS, SOURCE_TABLES
from yaml_generation import db_rules
from yaml_generation.db_rules import RULES, SOURCE_TYPES, kind_of, normalize_type, quote

PG, MS, ATH, SNOW = RULES["postgres"], RULES["mssql"], RULES["athena"], RULES["snowflake"]


@pytest.mark.parametrize("raw, expected", [
    ("DECIMAL(10,2)", "decimal"),
    ("timestamp(3) without time zone", "timestamp without time zone"),
    ("character varying(255)", "character varying"),
    ("  NVARCHAR( 50 ) ", "nvarchar"),
    (None, ""),
])
def test_normalize_type_drops_lengths_and_case(raw, expected):
    assert normalize_type(raw) == expected


@pytest.mark.parametrize("rules, dtype, kind", [
    (PG, "boolean", "boolean"),
    (PG, "timestamp with time zone", "timestamp"),
    (PG, "date", "date"),
    (PG, "character varying(20)", "text"),
    (PG, "integer", "integer"),
    (PG, "jsonb", "json"),
    (PG, "hstore", "json"),
    (PG, "money", "other"),
    (MS, "bit", "boolean"),
    (MS, "datetime2(3)", "timestamp"),
    (MS, "ntext", "large_text"),
    (MS, "nvarchar", "text"),          # INFORMATION_SCHEMA reports the type without its length
    (MS, "geography", "other"),
    (ATH, "timestamp", "timestamp"),
    (ATH, "tinyint", "integer"),
    (SNOW, "TIMESTAMP_NTZ", "timestamp"),
    (SNOW, "NUMBER", "integer"),
])
def test_kind_of_maps_each_database_type_to_its_kind(rules, dtype, kind):
    assert kind_of(dtype, rules) == kind


@pytest.mark.parametrize("source", SOURCE_TYPES + ["snowflake"])
def test_every_type_name_in_the_rules_is_lower_case(source):
    """kind_of() compares lower-case names. A capitalised entry would never match, and the column would be 'other'."""
    rules = RULES[source]
    for key in ("boolean_types", "timestamp_types", "date_types", "text_types", "large_text_types",
                "integer_types", "refused_types"):
        for name in rules[key]:
            assert name == name.lower(), f"{source}.{key}: {name!r} must be lower case"


@pytest.mark.parametrize("source", SOURCE_TYPES)
def test_every_source_block_has_the_keys_the_generator_reads(source):
    for key in ("label", "quote", "text_type", "true", "false", "trim", "timestamp", "date", "has_primary_keys",
                "boolean_types", "timestamp_types", "date_types", "text_types", "large_text_types",
                "integer_types", "refused_types"):
        assert key in RULES[source], f"{source} is missing {key}"


def test_quote_doubles_the_closing_character():
    assert quote('a"b', PG) == '"a""b"'
    assert quote("a]b", MS) == "[a]]b]"


def test_list_columns_reads_name_and_type_in_order():
    db = FakeDB(SOURCE_TABLES)
    assert db_rules.list_columns(db, "edge", "units")[:2] == [("id", "integer"), ("unit_name", "character varying")]


def test_missing_table_gives_no_columns():
    assert db_rules.list_columns(FakeDB(SOURCE_TABLES), "edge", "absent") == []


def test_names_are_escaped_in_the_catalogue_query():
    db = FakeDB({})
    db_rules.list_columns(db, "o'brien", "it's")
    assert "table_schema = 'o''brien' AND table_name = 'it''s'" in db.queries[0]


def test_list_tables_uses_the_prefix_for_snowflake():
    db = FakeDB({("EDGE", "UNITS"): []})
    db_rules.list_tables(db, "EDGE", prefix="DEV_EDGE_BRONZE.")
    assert "FROM DEV_EDGE_BRONZE.INFORMATION_SCHEMA.TABLES" in db.queries[0]


def test_list_tables_is_sorted_and_only_base_tables():
    db = FakeDB(SOURCE_TABLES)
    assert db_rules.list_tables(db, "edge") == ["lookup", "notes", "units"]
    assert "table_type = 'BASE TABLE'" in db.queries[0]


def test_primary_key_read_in_key_order():
    db = FakeDB(SOURCE_TABLES, PRIMARY_KEYS)
    assert db_rules.primary_key_columns(db, "edge", "units", PG) == ["id"]


def test_athena_has_no_primary_key_read_and_asks_nothing():
    db = FakeDB(SOURCE_TABLES, PRIMARY_KEYS)
    assert db_rules.primary_key_columns(db, "edge", "units", ATH) == []
    assert db.queries == []


def test_postgres_primary_key_uses_pg_index_not_information_schema():
    """information_schema hides the key from a read-only role (see the comment in db_rules.py), so pg_index is read."""
    db = FakeDB(SOURCE_TABLES, PRIMARY_KEYS)
    db_rules.primary_key_columns(db, "edge", "units", PG)
    assert "pg_index" in db.queries[0] and "nspname = 'edge'" in db.queries[0]


def test_postgres_tables_and_columns_are_read_from_pg_catalog():
    # information_schema hides what the role cannot read, so a visible table could look missing. pg_catalog does not.
    db = FakeDB(SOURCE_TABLES)
    assert db_rules.list_tables(db, "edge", rules=db_rules.RULES["postgres"]) == ["lookup", "notes", "units"]
    columns = db_rules.list_columns(db, "edge", "units", rules=db_rules.RULES["postgres"])
    assert columns[:2] == [("id", "integer"), ("unit_name", "character varying")]
    assert "pg_attribute" in db.queries[-1] and "INFORMATION_SCHEMA" not in db.queries[-1]
