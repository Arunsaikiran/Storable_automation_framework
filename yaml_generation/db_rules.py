"""Rules for each database: how to read its catalogue, and how to write a value as text.

One block per database ENGINE in RULES, not per application. The generator uses the SOURCE block of the layer's engine
(--layer_type bronze_postgres uses "postgres"), and the TARGET block, which is always "snowflake" for now.

Where a table lives (application, schema, Snowflake database and schema) is not here: it is in the filter workbooks
(filter_files.py), one row per table. A new application on an engine that is already here needs only its workbook. A new
engine (MySQL, MongoDB) needs one block here, a connector in db/, and its layer in main.py.

What each key means
  label                 the engine's name: shown in messages, and the value of source_database in a filter workbook
  has_primary_keys      whether the catalogue has primary keys the generator can read
  pg_catalog            read tables, columns and keys from pg_catalog, not information_schema (PostgreSQL only).
                        information_schema leaves out whatever the role cannot read, so a table would look missing.
  quote                 how a name is quoted, as (open, close)
  text_type             the type a value is cast to, so both sides become text
  true, false           how TRUE and FALSE are written
  trim, timestamp, date SQL templates; {c} is replaced by the column
  boolean_types, timestamp_types, date_types, text_types, large_text_types, integer_types
                        type names as this database reports them
  refused_types         types that cannot be compared as text here. A data file for such a table is refused.
  schema_query          the catalogue query of the schema validation YAML (sourcequery / targetquery), today's text.
                        {schema} and {database} are filled from the table's filter workbook row (schema_query());
                        {table_name} is left for validators/schema_validation.py to fill per table.

Type names are compared in lower case, with lengths removed: "decimal(10,2)" is read as "decimal".
Values here are the best understanding of each dialect. Only PostgreSQL has been run on a live
database. The others must be checked on the real server before they are relied on (see the guide).
"""

import re

RULES = {
    "postgres": {
        "label": "PostgreSQL",
        "has_primary_keys": True,
        "pg_catalog": True,
        "quote": ('"', '"'),
        "text_type": "TEXT",
        "true": "true",
        "false": "false",
        "trim": "TRIM({c})",
        "timestamp": "TO_CHAR({c}, 'YYYY-MM-DD HH24:MI:SS.US')",
        "date": "TO_CHAR({c}, 'YYYY-MM-DD')",
        "boolean_types": {"boolean"},
        "timestamp_types": {"timestamp without time zone", "timestamp with time zone"},
        "date_types": {"date"},
        "text_types": {"character varying", "character", "text"},
        "large_text_types": set(),
        "integer_types": {"integer", "bigint", "smallint"},
        # Compared as flattened JSON text (utils/json_normalize.py): keys and spacing differ between the two databases.
        "json_types": {"jsonb", "json", "hstore"},
        "refused_types": set(),
        "schema_query": (
            "SELECT\n"
            "table_name,column_name,data_type,character_maximum_length,character_octet_length,numeric_precision,"
            "numeric_precision_radix,numeric_scale,datetime_precision\n"
            "FROM {database}.information_schema.columns\n"
            "where table_schema = '{schema}' and table_name = '{table_name}'\n"
            "order by table_name,ordinal_position;"),
    },
    "mssql": {
        "label": "SQL Server",
        "has_primary_keys": True,
        "quote": ("[", "]"),
        "text_type": "NVARCHAR(MAX)",
        "true": "1",
        "false": "0",
        # LTRIM(RTRIM()) works on every SQL Server version. TRIM() needs SQL Server 2017 or later.
        "trim": "LTRIM(RTRIM({c}))",
        # FORMAT follows the login's language: a Thai login gets the Buddhist year (2568). The culture is pinned.
        "timestamp": "FORMAT({c}, 'yyyy-MM-dd HH:mm:ss.ffffff', 'en-US')",
        "date": "FORMAT({c}, 'yyyy-MM-dd', 'en-US')",
        "boolean_types": {"bit"},
        "timestamp_types": {"datetime", "datetime2", "smalldatetime"},
        "date_types": {"date"},
        "text_types": {"varchar", "nvarchar", "char", "nchar"},
        # text and ntext cannot be passed to TRIM(), so they are cast without trimming.
        "large_text_types": {"text", "ntext"},
        "integer_types": {"int", "bigint", "smallint", "tinyint"},
        # rowversion is reported as "timestamp": a binary counter with no match in Snowflake.
        "refused_types": {"geography", "geometry", "image", "timestamp"},
        "schema_query": (
            "SELECT\n"
            "TABLE_NAME AS table_name,\n"
            "COLUMN_NAME AS column_name,\n"
            "DATA_TYPE AS data_type,\n"
            "CHARACTER_MAXIMUM_LENGTH AS character_maximum_length,\n"
            "CHARACTER_OCTET_LENGTH AS character_octet_length,\n"
            "NUMERIC_PRECISION AS numeric_precision,\n"
            "NUMERIC_PRECISION_RADIX AS numeric_precision_radix,\n"
            "NUMERIC_SCALE AS numeric_scale,\n"
            "DATETIME_PRECISION AS datetime_precision\n"
            "FROM {database}.INFORMATION_SCHEMA.COLUMNS\n"
            "WHERE TABLE_SCHEMA = '{schema}'\n"
            "AND TABLE_NAME = '{table_name}'\n"
            "ORDER BY TABLE_NAME,ORDINAL_POSITION;"),
    },
    "athena": {
        "label": "Athena",
        # Athena tables have no primary key. Name the key in the table rules file (key_columns).
        "has_primary_keys": False,
        "quote": ('"', '"'),
        "text_type": "VARCHAR",
        "true": "true",
        "false": "false",
        "trim": "TRIM({c})",
        "timestamp": "date_format({c}, '%Y-%m-%d %H:%i:%s.%f')",
        "date": "date_format({c}, '%Y-%m-%d')",
        "boolean_types": {"boolean"},
        "timestamp_types": {"timestamp"},
        "date_types": {"date"},
        "text_types": {"varchar", "char"},
        "large_text_types": set(),
        "integer_types": {"integer", "bigint", "smallint", "tinyint"},
        "refused_types": set(),
    },
    "redshift": {
        "label": "Redshift",
        "has_primary_keys": True,    # Redshift keys are informational; read them when they are defined
        "quote": ('"', '"'),
        # Without a length, Redshift's CAST gives VARCHAR(256) and cuts longer text. 65535 is the maximum.
        "text_type": "VARCHAR(65535)",
        "true": "true",
        "false": "false",
        "trim": "TRIM({c})",
        "timestamp": "TO_CHAR({c}, 'YYYY-MM-DD HH24:MI:SS.US')",
        "date": "TO_CHAR({c}, 'YYYY-MM-DD')",
        "boolean_types": {"boolean"},
        "timestamp_types": {"timestamp without time zone", "timestamp with time zone"},
        "date_types": {"date"},
        "text_types": {"character varying", "character", "text"},
        "large_text_types": set(),
        "integer_types": {"integer", "bigint", "smallint"},
        "refused_types": set(),
    },
    # The target. Always Snowflake for now.
    "snowflake": {
        "label": "Snowflake",
        "has_primary_keys": False,   # the target is only read for its column names
        "quote": ('"', '"'),
        "text_type": "STRING",
        "true": "TRUE",
        "false": "FALSE",
        "trim": "TRIM({c})",
        "timestamp": "TO_VARCHAR({c}, 'YYYY-MM-DD HH24:MI:SS.FF6')",
        "date": "TO_VARCHAR({c}, 'YYYY-MM-DD')",
        "boolean_types": {"boolean"},
        "timestamp_types": {"timestamp_ntz", "timestamp_ltz", "timestamp_tz"},
        "date_types": {"date"},
        "text_types": {"text", "varchar"},
        "large_text_types": set(),
        "integer_types": {"number"},
        "refused_types": set(),
        "schema_query": (
            "SELECT\n"
            "table_name,column_name,data_type,character_maximum_length,character_octet_length,numeric_precision,"
            "numeric_precision_radix,numeric_scale,datetime_precision\n"
            "FROM {database}.information_schema.columns\n"
            "WHERE TABLE_CATALOG = '{database}' AND table_schema = '{schema}' AND TABLE_NAME = '{table_name}'\n"
            "ORDER BY table_name, ordinal_position;"),
    },

    "newbook": {
        "label": "Newbook",
        "has_primary_keys": False,   # the target is only read for its column names
        "quote": ('"', '"'),
        "text_type": "STRING",
        "true": "TRUE",
        "false": "FALSE",
        "trim": "TRIM({c})",
        "timestamp": "TO_VARCHAR({c}, 'YYYY-MM-DD HH24:MI:SS.FF6')",
        "date": "TO_VARCHAR({c}, 'YYYY-MM-DD')",
        "boolean_types": {"boolean"},
        "timestamp_types": {"timestamp_ntz", "timestamp_ltz", "timestamp_tz"},
        "date_types": {"date"},
        "text_types": {"text", "varchar"},
        "large_text_types": set(),
        "integer_types": {"number"},
        "refused_types": set(),
        "schema_query": (
            "SELECT\n"
            "table_name,column_name,data_type,character_maximum_length,character_octet_length,numeric_precision,"
            "numeric_precision_radix,numeric_scale,datetime_precision\n"
            "FROM {database}.information_schema.columns\n"
            "WHERE TABLE_CATALOG = '{database}' AND table_schema = '{schema}' AND TABLE_NAME = '{table_name}'\n"
            "ORDER BY table_name, ordinal_position;"),
    },
}



SOURCE_TYPES = ["postgres", "mssql", "athena", "redshift", "newbook"]
TARGET_TYPES = ["snowflake"]

# Lengths and precisions in a type name, e.g. "(10,2)" or "(3)".
_LENGTH = re.compile(r"\(\s*\d+\s*(,\s*\d+\s*)?\)")


def normalize_type(dtype) -> str:
    """'DECIMAL(10,2)' -> 'decimal'; 'timestamp(3) without time zone' -> 'timestamp without time zone'."""
    return " ".join(_LENGTH.sub("", dtype or "").lower().split())


def kind_of(dtype, rules) -> str:
    """One of: boolean, timestamp, date, text, large_text, integer, json, other.

    'json' columns are cast to text as they are, and the validator flattens them (json_columns in the YAML).
    'other' columns have no format rule. They are cast to text as they are, and the header warns about them.
    """
    t = normalize_type(dtype)
    if t in rules["boolean_types"]:
        return "boolean"
    if t in rules["timestamp_types"]:
        return "timestamp"
    if t in rules["date_types"]:
        return "date"
    if t in rules["text_types"]:
        return "text"
    if t in rules["large_text_types"]:
        return "large_text"
    if t in rules["integer_types"]:
        return "integer"
    if t in rules.get("json_types", set()):
        return "json"
    return "other"


def quote(name, rules) -> str:
    """Quote a name exactly as the database spells it, so reserved words such as "order" still work."""
    open_, close = rules["quote"]
    return open_ + name.replace(close, close + close) + close


def _lit(value) -> str:
    """A SQL string literal. Names come from the database's own catalogue or the filter workbooks, so quotes are doubled."""
    return "'" + str(value).replace("'", "''") + "'"


def _pg_catalog(rules) -> bool:
    """True for PostgreSQL, whose tables, columns and keys are read from pg_catalog (see the rules block above)."""
    return bool(rules and rules.get("pg_catalog"))


def schema_query(rules, schema, database=None) -> str:
    """The schema validation query for one engine, at one location. {table_name} is kept for the validator to fill.

    Raises ValueError for an engine with no schema_query (Athena, Redshift: no schema validation YAML yet).
    """
    if "schema_query" not in rules:
        raise ValueError(f"{rules['label']} has no schema_query in db_rules.RULES, so no schema validation YAML can be written")
    return rules["schema_query"].replace("{schema}", schema).replace("{database}", database or "")


def list_tables(db, schema, prefix="", rules=None):
    """Base table names in one schema, sorted. Used by --tables all."""
    if _pg_catalog(rules):
        sql = (
            "SELECT c.relname FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
            f"WHERE n.nspname = {_lit(schema)} AND c.relkind IN ('r', 'p') ORDER BY c.relname"
        )
    else:
        sql = (
            f"SELECT table_name FROM {prefix}INFORMATION_SCHEMA.TABLES "
            f"WHERE table_schema = {_lit(schema)} AND table_type = 'BASE TABLE' "
            f"ORDER BY table_name"
        )
    return [row[0] for row in db.execute_query(sql).values.tolist()]


def list_columns(db, schema, table, prefix="", rules=None):
    """(column_name, data_type) pairs in column order. An empty list means the table does not exist.

    Read by position, not by name: PostgreSQL returns lower-case column names, Snowflake and SQL Server upper case.
    PostgreSQL types are read as format_type() prints them (lengths are removed by normalize_type).
    """
    if _pg_catalog(rules):
        sql = (
            "SELECT a.attname, format_type(a.atttypid, a.atttypmod) "
            "FROM pg_attribute a JOIN pg_class c ON c.oid = a.attrelid JOIN pg_namespace n ON n.oid = c.relnamespace "
            f"WHERE n.nspname = {_lit(schema)} AND c.relname = {_lit(table)} "
            "AND a.attnum > 0 AND NOT a.attisdropped ORDER BY a.attnum"
        )
    else:
        sql = (
            f"SELECT column_name, data_type FROM {prefix}INFORMATION_SCHEMA.COLUMNS "
            f"WHERE table_schema = {_lit(schema)} AND table_name = {_lit(table)} "
            f"ORDER BY ordinal_position"
        )
    return [(name, dtype) for name, dtype in db.execute_query(sql).values.tolist()]


def primary_key_columns(db, schema, table, rules) -> list:
    """Primary-key column names in key order. Empty when there is no key, or the database has none to read.

    PostgreSQL is read from pg_catalog, like the tables and columns. information_schema leaves out a constraint the
    role cannot see, so a table with a real key was reported as having none (seen on the dev database: units_pkey).
    """
    if not rules["has_primary_keys"]:
        return []
    if _pg_catalog(rules):
        sql = (
            "SELECT a.attname "
            "FROM pg_index i "
            "JOIN pg_class c ON c.oid = i.indrelid "
            "JOIN pg_namespace n ON n.oid = c.relnamespace "
            "JOIN pg_attribute a ON a.attrelid = c.oid AND a.attnum = ANY(i.indkey) "
            f"WHERE n.nspname = {_lit(schema)} AND c.relname = {_lit(table)} AND i.indisprimary "
            "ORDER BY array_position(i.indkey::int2[], a.attnum)"
        )
    else:
        sql = (
            "SELECT kcu.column_name "
            "FROM INFORMATION_SCHEMA.TABLE_CONSTRAINTS tc "
            "JOIN INFORMATION_SCHEMA.KEY_COLUMN_USAGE kcu "
            "ON kcu.constraint_name = tc.constraint_name "
            "AND kcu.table_schema = tc.table_schema AND kcu.table_name = tc.table_name "
            "WHERE tc.constraint_type = 'PRIMARY KEY' "
            f"AND tc.table_schema = {_lit(schema)} AND tc.table_name = {_lit(table)} "
            "ORDER BY kcu.ordinal_position"
        )
    return [row[0] for row in db.execute_query(sql).values.tolist()]
