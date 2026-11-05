"""The filter workbooks in yaml_generation/Filter-files-excel/: the one source of truth for where each table lives, and its row filters.

One workbook per layer, picked by --layer_type (WORKBOOKS below): bronze_postgres reads edge_bronze_filters.xlsx and
bronze_mssql reads sitelink_bronze_filters.xlsx. No other .xlsx in the folder is read. A new layer (bronze_athena) is a new
workbook with the same columns, one line in WORKBOOKS, and a block for its engine in db_rules.RULES.

What is read: the first sheet that has the table column and the six location columns. Headers are matched trimmed, in
lower case, with a trailing dot ignored. One row per table:
  Target Table Name              the table name (the source name; Snowflake holds it in upper case)
  source_application             e.g. Edge, SiteLink. Shown in the log.
  source_database                the source engine, as db_rules.RULES names it: PostgreSQL, SQL Server (the label, or the key)
  source_schema                  e.g. public, dbo
  target_application             Snowflake (the only target for now)
  target_database                the Snowflake database as written in the YAML, e.g. {env}_EDGE_BRONZE. {env} is filled in per
                                 run, and a row without it is refused (it would compare every environment against one database).
  target_schema                  e.g. STOREDGE_FMS_PUBLIC
  Historical Filter to apply     the row filter for --run_type historical (optional)
  Incremental filters to apply   the row filter for --run_type incremental (optional)
Every row's source_database must be the layer's engine (bronze_postgres: PostgreSQL). --tables all is every row. A named
table with no row FAILS: without a row there is no schema to read it from. Table names are matched without regard to case
(--tables addresses is the row Addresses), and the workbook's spelling is used from then on.

The type mapping sheet (SCHEMA_SHEETS: 'MSSQL schema mapping', 'Postgres schema mapping') is read by schema_mapping() for
the schema validation YAML only. One row per source type: source_data_type, target_data_type, precision_check, notes.

Why filters: a filter keeps part of a large table, so the framework does not load the whole table into RAM. The filter is
applied to the source and to Snowflake, for the count and the data, the same way as a filter in the rules workbook
(settings.py). When the rules workbook sets a filter for the same side, that one is used. --filter_files no reads the
locations and ignores the filters.

The filter cell
  Each cell holds the table, its alias, the joins and the conditions, one clause per line:

      general_ledger_line_items j
      join facilities f on j.facility_id = f.id
      where f.company_id in (137,9088,501) and j.created_at >= '2024-08-01'

  The cell does not start with "select count(*) from": the framework adds it. A cell that does start with "select"
  is read as it is.

  It becomes one condition per side. Each join becomes a sub-select on the joined column:

      created_at >= '2024-08-01' AND facility_id IN (SELECT id FROM "public"."facilities" WHERE company_id IN (137,9088,501))

  The key need not be id. SiteLink joins on its own keys, through Sites to Owners:

      Units t
      join Sites s on t.SiteID = s.SiteID
      join Owners o on s.OwnerID = o.OwnerID
      where o.sCorpCode in ('SLQA', 'STP2DEV')

  becomes  SiteID IN (SELECT SiteID FROM [dbo].[Sites] WHERE OwnerID IN (SELECT OwnerID FROM [dbo].[Owners] WHERE sCorpCode in (...)))

  The Snowflake side is built from the same cell, so both sides keep the same rows. A sub-select keeps each row once, as
  a filter should, even where an inner join on a key that is not unique would repeat it.

Shapes read
  - select count(*) from <table> [alias], then inner joins, then an optional where clause joined with AND.
  - Each join is  <table> [alias] on <a>.<column> = <alias>.<key>, where <a> is already in the statement.
  - A condition uses one table. A bare column belongs to the first table. Joins chain (a table joined to a joined table).
  - A cell with only conditions, no table line and no join (e.g. created_at >= '2026-08-01'), is about the row's table.

What is refused, and the table is then loaded in full (the log names the row)
  OR, BETWEEN, left/right/outer joins, subqueries, group by, quoted identifiers, unbalanced quotes or brackets,
  a condition that uses two tables, and anything check_filter() refuses.

A cell that says "compare full" is no filter. A bind parameter such as :scorpcode has no value here, so that statement is
refused and its table is loaded in full.

Self-check: python yaml_generation/filter_files.py
"""

import logging
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

from openpyxl import load_workbook

FRAMEWORK_ROOT = Path(__file__).resolve().parents[1]
if str(FRAMEWORK_ROOT) not in sys.path:  # lets the self-check run as a plain script
    sys.path.insert(0, str(FRAMEWORK_ROOT))

from yaml_generation.db_rules import RULES, quote  # noqa: E402
from yaml_generation.yaml_builder import ENV_PLACEHOLDER, FIVETRAN_FILTER, check_filter  # noqa: E402

FOLDER = Path(__file__).resolve().parent / "Filter-files-excel"
# The workbook each layer reads, by its engine (--layer_type bronze_<engine>). A new layer adds its workbook here.
WORKBOOKS = {"postgres": "edge_bronze_filters.xlsx", "mssql": "sitelink_bronze_filters.xlsx"}
# The headers, as matched: trimmed, lower case, a trailing dot dropped. The same layout for every application.
TABLE_HEADER = "target table name"
LOCATION_HEADERS = ["source_application", "source_database", "source_schema",
                    "target_application", "target_database", "target_schema"]
FILTER_HEADERS = {"historical": "historical filter to apply", "incremental": "incremental filters to apply"}
# The type mapping sheet of each workbook, read for the schema validation YAML (schema_mapping()). Matched without regard to case.
SCHEMA_SHEETS = {"mssql": "MSSQL schema mapping", "postgres": "Postgres schema mapping"}
MAPPING_HEADERS = ["source_data_type", "target_data_type"]          # required; precision_check and notes are optional
PRECISION_CHECKS = ("datetime", "numeric", "character")


@dataclass(frozen=True)
class Location:
    """Where one table lives, from its workbook row. Tables with the same Location are generated together."""

    application: str       # e.g. Edge
    source_type: str       # the db_rules.RULES key of source_database, e.g. postgres
    source_schema: str     # e.g. public
    target_database: str   # as written in the YAML, e.g. {env}_EDGE_BRONZE
    target_schema: str     # e.g. STOREDGE_FMS_PUBLIC


@dataclass
class Row:
    """One table's row: its location, and its filter cell for the run type ("" when none)."""

    table: str
    location: Location
    statement: str
    label: str             # "<file> row <n> (<table>)", for the log

# A quoted literal. Text inside quotes is never read as SQL, and never changed.
LITERAL = re.compile(r"'[^']*'")
# alias.column, as written in a condition.
QUALIFIED = re.compile(r"\b([A-Za-z_]\w*)\.(\w+)\b")
# The table in a FROM or JOIN part, with an optional alias.
TABLE = re.compile(r"^(\w+)(?:\s+(\w+))?$")
# The table a count statement is about: the name right after 'from'.
BASE_TABLE = re.compile(r"select\s+count\s*\(\s*\*\s*\)\s+from\s+(\w+)", re.IGNORECASE)
# One join: <table> [alias] on <a>.<column> = <b>.<column>.
JOIN_ON = re.compile(r"^(\w+)(?:\s+(\w+))?\s+on\s+(\w+)\.(\w+)\s*=\s*(\w+)\.(\w+)$", re.IGNORECASE)
# Words that change what the count means. Checked outside quotes only.
REFUSED_WORDS = re.compile(
    r"\b(or|between|exists|left|right|full|outer|cross|group|order|having|limit|union|distinct|select)\b",
    re.IGNORECASE)


class Refused(ValueError):
    """A statement this reader does not understand. The message says which part."""


@dataclass
class _Plan:
    """The tables and conditions of one statement, before they are written for a side."""

    base: str                                          # alias of the first table
    tables: dict = field(default_factory=dict)         # alias -> table name, as written
    own: dict = field(default_factory=dict)            # alias -> conditions that use only that table
    joins: dict = field(default_factory=dict)          # alias -> [(column, parent alias, parent key)], one sub-select each


@dataclass
class _Side:
    """How one side writes its tables. active is added inside every sub-select on that side."""

    ref: object                  # function: table name -> the name written after FROM / IN (SELECT ... FROM ...)
    active: str = None


def _outside_quotes(text, change):
    """Apply change() to the parts of text outside quotes. Quoted text is kept as it is."""
    parts = re.split(r"('[^']*')", text)
    return "".join(part if i % 2 else change(part) for i, part in enumerate(parts))


def _masked(text):
    """The text with each quote's contents removed, so a keyword or a dot inside quotes is not read."""
    return LITERAL.sub("''", text)


def _split_and(text):
    """Split a WHERE clause at each AND that is outside quotes."""
    pieces, start = [], 0
    for match in re.finditer(r"'[^']*'|\band\b", text, re.IGNORECASE):
        if match.group(0).startswith("'"):
            continue
        pieces.append(text[start:match.start()])
        start = match.end()
    pieces.append(text[start:])
    return [piece.strip() for piece in pieces]


def _plan(statement) -> _Plan:
    """Read one count statement into a _Plan. Raises Refused with the reason."""
    # Whitespace is collapsed outside quotes only, so a literal such as 'a  b' keeps its spaces.
    text = _outside_quotes(statement.strip(), lambda s: re.sub(r"\s+", " ", s)).strip()
    if text.endswith(";"):
        text = text[:-1].strip()
    if text.count("'") % 2:
        raise Refused("unbalanced quote")
    match = re.match(r"select\s+count\s*\(\s*\*\s*\)\s+from\s+(.+)$", text, re.IGNORECASE)
    if not match:
        raise Refused("only 'select count(*) from ...' statements are read")
    body = match.group(1)
    masked = _masked(body)
    if '"' in masked:
        raise Refused("quoted identifiers are not read")
    if re.search(r"(?<!:):[A-Za-z_]", masked):  # not a PostgreSQL cast (::date)
        raise Refused("a bind parameter such as :name has no value in the framework")
    if masked.count("(") != masked.count(")"):
        raise Refused("brackets do not balance")
    word = REFUSED_WORDS.search(masked)
    if word:
        raise Refused(f"'{word.group(0)}' is not read")

    parts = re.split(r"\s+where\s+", body, maxsplit=1, flags=re.IGNORECASE)
    from_text, where_text = parts[0], (parts[1] if len(parts) > 1 else None)
    join_parts = re.split(r"\s+(?:inner\s+)?join\s+", from_text, flags=re.IGNORECASE)

    base = TABLE.match(join_parts[0].strip())
    if not base:
        raise Refused(f"the table is not read: '{join_parts[0].strip()}'")
    alias = (base.group(2) or base.group(1)).lower()
    plan = _Plan(base=alias)
    plan.tables[alias] = base.group(1)
    plan.own[alias] = []

    for piece in join_parts[1:]:
        join = JOIN_ON.match(piece.strip())
        if not join:
            raise Refused(f"join not read: '{piece.strip()}'. Write it as: join <table> <alias> on <alias>.<column> = <alias>.<key>")
        new_table, new_alias = join.group(1), (join.group(2) or join.group(1)).lower()
        # Aliases are matched in lower case; column names are kept as written (SiteID stays SiteID).
        a_alias, a_col, b_alias, b_col = join.group(3).lower(), join.group(4), join.group(5).lower(), join.group(6)
        if new_alias in plan.tables:
            raise Refused(f"alias '{new_alias}' is used twice")
        # One side is the joined table's key column (id, SiteID, OwnerID ...); the other is a table already in the statement.
        if a_alias == new_alias and b_alias in plan.tables:
            old, column, key = b_alias, b_col, a_col
        elif b_alias == new_alias and a_alias in plan.tables:
            old, column, key = a_alias, a_col, b_col
        else:
            raise Refused(f"join '{piece.strip()}' must link a table already in the statement to a column of {new_table}")
        plan.tables[new_alias] = new_table
        plan.own[new_alias] = []
        plan.joins.setdefault(old, []).append((column, new_alias, key))

    for condition in (_split_and(where_text) if where_text else []):
        if not condition:
            raise Refused("an empty condition after WHERE or AND")
        masked = _masked(condition)
        if masked.count("(") != masked.count(")"):
            raise Refused(f"brackets do not balance in '{condition}'")
        used = {alias_name.lower() for alias_name, _ in QUALIFIED.findall(masked)}
        unknown = used - set(plan.tables)
        if unknown:
            raise Refused(f"'{sorted(unknown)[0]}' is not a table or alias in the statement: '{condition}'")
        if len(used) > 1:
            raise Refused(f"a condition uses two tables; write it as a join: '{condition}'")
        owner = used.pop() if used else plan.base
        # The alias is dropped: inside a sub-select the column belongs to that sub-select's table.
        plan.own[owner].append(_outside_quotes(
            condition, lambda s: re.sub(rf"\b{owner}\.(?=\w)", "", s, flags=re.IGNORECASE)))
    return plan


def _where(plan: _Plan, alias: str, side: _Side) -> list:
    """The conditions for one table: its own, then one sub-select for each join from it to a parent table."""
    parts = list(plan.own[alias])
    for column, parent, key in plan.joins.get(alias, []):
        inner = _where(plan, parent, side)
        if side.active:
            inner.append(side.active)
        sub = f"SELECT {key} FROM {side.ref(plan.tables[parent])}"
        if inner:
            sub += " WHERE " + " AND ".join(inner)
        parts.append(f"{column} IN ({sub})")
    return parts


def convert(statement, source_type, source_schema, target_database, target_schema):
    """(source_where, target_where) for one count statement. A side is None when the statement adds no condition.

    Raises Refused when the statement is not read. Tables without a schema are read in source_schema (source) and
    target_database.target_schema (Snowflake), as the rest of the generator does.
    """
    plan = _plan(statement)
    pg, snow = RULES[source_type], RULES["snowflake"]
    source = _Side(ref=lambda table: f"{quote(source_schema, pg)}.{quote(table, pg)}")
    # Snowflake keeps history rows, so a sub-select reads only the active ones (the rule the generator uses for the base table).
    # ponytail: no check that the join and condition columns exist. A wrong column fails at the count with a SQL error.
    target = _Side(ref=lambda table: f"{target_database}.{quote(target_schema, snow)}.{quote(table.upper(), snow)}",
                   active=FIVETRAN_FILTER)
    return _join(_where(plan, plan.base, source)), _join(_where(plan, plan.base, target))


def _join(parts):
    return " AND ".join(parts) if parts else None


def _header(name) -> str:
    """A header as matched: trimmed, lower case, without a trailing dot ("Historical Filter to apply." matches)."""
    return str(name).strip().lower().rstrip(".").strip() if name is not None else ""


def engine_of(name):
    """The db_rules.RULES key for an engine as a workbook writes it (the label or the key, any case), or None."""
    text = str(name or "").strip().lower()
    return next((key for key, rule in RULES.items() if text in (key, rule["label"].lower())), None)


def _sheet_rows(path):
    """({header: column}, [(row_number, values)]) of the first sheet with the table and location columns, or None."""
    workbook = load_workbook(path, read_only=True, data_only=True)
    try:
        for sheet in workbook.worksheets:
            rows = sheet.iter_rows(values_only=True)
            header = [_header(name) for name in (next(rows, None) or ())]
            if TABLE_HEADER in header and all(name in header for name in LOCATION_HEADERS):
                return {name: header.index(name) for name in header if name}, list(enumerate(rows, start=2))
        return None
    finally:
        workbook.close()


def _count_statement(text, table):
    """The cell as a count statement. The cell holds no 'select count(*) from' (the framework adds it), so the parser reads it as before.

    A cell whose first line is not a table name (with an optional alias) holds only conditions on the row's table, so the table
    goes first: "created_at >= '2026-08-01'" becomes "<table> where created_at >= '2026-08-01'".
    """
    text = text.strip()
    if text.lower().startswith("select"):
        return text
    first_line = text.split("\n", 1)[0].strip()
    if re.match(r"where\b", first_line, re.IGNORECASE) or not TABLE.match(first_line):
        text = f"{table} where " + re.sub(r"^where\s+", "", text, flags=re.IGNORECASE)
    return "select count(*) from " + text


def read(source_type, run_type, folder=None, log=None) -> dict:
    """{table: Row} for every row of the layer's workbook (WORKBOOKS[source_type]), in workbook order.

    Raises ValueError naming the file (and row) when the layer has no workbook, the file or its sheet is missing, or a row is
    bad: an empty location column, a source_database that is not the layer's engine, a target that is not Snowflake, or a
    target_database without {env}. run_type picks the filter column (FILTER_HEADERS). A table listed twice (in any case)
    keeps its first row.
    """
    log = log or logging.getLogger(__name__)
    label_of = RULES[source_type]["label"]
    name = WORKBOOKS.get(source_type)
    if name is None:
        raise ValueError(f"--layer_type bronze_{source_type} has no filter workbook: add <application>_bronze_filters.xlsx "
                         f"to {FOLDER} and its name to filter_files.WORKBOOKS")
    path = Path(folder or FOLDER) / name
    if not path.is_file():
        raise ValueError(f"the filter workbook for --layer_type bronze_{source_type} is missing: {path}")
    sheet = _sheet_rows(path)
    if sheet is None:
        raise ValueError(f"{name} has no sheet with the columns {', '.join([TABLE_HEADER] + LOCATION_HEADERS)}")
    position, rows = sheet
    filter_at = position.get(FILTER_HEADERS[run_type])
    if filter_at is None:
        log.warning("Filter workbook %s has no '%s' column, so its tables get no %s filter", name, FILTER_HEADERS[run_type], run_type)
    found, first = {}, {}
    for number, values in rows:
        def cell(index):
            value = values[index] if index is not None and index < len(values) else None
            return str(value).strip() if value is not None and str(value).strip() else ""

        table = cell(position[TABLE_HEADER])
        if not table:
            continue
        label = f"{name} row {number} ({table})"
        missing = [column for column in LOCATION_HEADERS if not cell(position[column])]
        if missing:
            raise ValueError(f"{label}: {', '.join(missing)} is empty. Every row names where its table lives.")
        engine = cell(position["source_database"])
        if engine_of(engine) != source_type:
            raise ValueError(f"{label}: source_database is {engine!r}, but {name} is the workbook for {label_of} "
                             f"(--layer_type bronze_{source_type})")
        if engine_of(cell(position["target_application"])) != "snowflake":
            raise ValueError(f"{label}: target_application must be Snowflake, found {cell(position['target_application'])!r}")
        target_database = cell(position["target_database"])
        if ENV_PLACEHOLDER not in target_database:
            raise ValueError(f"{label}: target_database {target_database!r} has no {ENV_PLACEHOLDER}, so every environment "
                             f"would be compared against that one database. Write it as {ENV_PLACEHOLDER}_<APPLICATION>_BRONZE")
        if table.lower() in first:
            log.warning("%s: the table is listed twice, so this row is not used (the first is %s)", label, first[table.lower()])
            continue
        first[table.lower()] = label
        statement = values[filter_at] if filter_at is not None and filter_at < len(values) else None
        location = Location(cell(position["source_application"]), source_type, cell(position["source_schema"]),
                            target_database, cell(position["target_schema"]))
        found[table] = Row(table, location, str(statement) if statement else "", label)
    log.info("Filter workbook %s: %d table(s) for %s", name, len(found), label_of)
    return found


def schema_mapping(source_type, folder=None, log=None):
    """(datatype_map, precision_checks) from the layer workbook's type mapping sheet (SCHEMA_SHEETS), for the schema YAML.

    datatype_map      {source_data_type: target_data_type}. The source type is kept as written: the validator looks up the
                      catalogue's DATA_TYPE exactly. The Snowflake type is written in lower case, as the validator compares it.
    precision_checks  {datetime | numeric | character: [source types]}, from the precision_check column (read in phase 2).
    Raises ValueError naming the file and row: no workbook, no sheet, a missing header, an empty source_data_type or
    target_data_type, or an unknown precision_check. A source type listed twice (in any case) keeps its first row.
    """
    log = log or logging.getLogger(__name__)
    name, sheet_name = WORKBOOKS.get(source_type), SCHEMA_SHEETS.get(source_type)
    if name is None or sheet_name is None:
        raise ValueError(f"--layer_type bronze_{source_type} has no type mapping sheet: add it to filter_files.SCHEMA_SHEETS")
    path = Path(folder or FOLDER) / name
    if not path.is_file():
        raise ValueError(f"the filter workbook for --layer_type bronze_{source_type} is missing: {path}")
    workbook = load_workbook(path, read_only=True, data_only=True)
    try:
        sheet = next((ws for ws in workbook.worksheets if ws.title.strip().lower() == sheet_name.lower()), None)
        if sheet is None:
            raise ValueError(f"{name} has no sheet '{sheet_name}' (the source to Snowflake type mapping)")
        rows = sheet.iter_rows(values_only=True)
        header = [_header(cell) for cell in (next(rows, None) or ())]
        missing = [column for column in MAPPING_HEADERS if column not in header]
        if missing:
            raise ValueError(f"{name}, sheet '{sheet.title}': no {', '.join(missing)} column")
        at = {column: header.index(column) for column in MAPPING_HEADERS + ["precision_check"] if column in header}
        datatype_map, precision_checks, first = {}, {}, {}
        for number, values in enumerate(rows, start=2):
            def cell(column):
                index = at.get(column)
                value = values[index] if index is not None and index < len(values) else None
                return str(value).strip() if value is not None else ""

            source, target, check = cell("source_data_type"), cell("target_data_type").lower(), cell("precision_check").lower()
            if not (source or target or check):
                continue
            label = f"{name}, sheet '{sheet.title}' row {number}"
            if not source or not target:
                raise ValueError(f"{label}: source_data_type and target_data_type must both be filled")
            if check and check not in PRECISION_CHECKS:
                raise ValueError(f"{label}: precision_check is {check!r}; expected one of {', '.join(PRECISION_CHECKS)} or empty")
            if source.lower() in first:
                log.warning("%s: type %s is listed twice, so this row is not used (the first is row %d)",
                            label, source, first[source.lower()])
                continue
            first[source.lower()] = number
            datatype_map[source] = target
            if check:
                precision_checks.setdefault(check, []).append(source)
    finally:
        workbook.close()
    if not datatype_map:
        raise ValueError(f"{name}, sheet '{sheet_name}': no type mapping row")
    log.info("Type mapping %s / %s: %d type(s)", name, sheet_name, len(datatype_map))
    return datatype_map, precision_checks


def spelled(tables, rows) -> list:
    """tables as the workbook writes them: a name is matched without regard to case. A name not in rows is kept as typed."""
    names = {table.lower(): table for table in rows}
    return [names.get(str(table).lower(), table) for table in tables]


def filters(rows: dict, log=None) -> dict:
    """{table: (source_where, target_where)} from the filter cells of rows (from read()). A table not in it is loaded in full.

    Each side is written for its row's own location, so the Snowflake side keeps the {env} placeholder. A refused cell is
    logged with its file and row, and that table is loaded in full.
    """
    log = log or logging.getLogger(__name__)
    found = {}
    for table, row in rows.items():
        if not row.statement.strip() or row.statement.strip().lower() == "compare full":
            continue
        where = row.location
        try:
            statement = _count_statement(row.statement, table)
            # The statement must be about the row's own table. A cell copied from another row would filter the wrong table.
            statement_table = BASE_TABLE.match(statement)
            if not statement_table or statement_table.group(1).lower() != table.lower():
                raise Refused(f"the statement is about '{statement_table.group(1) if statement_table else '?'}', not '{table}'")
            source_where, target_where = convert(statement, where.source_type, where.source_schema,
                                                 where.target_database, where.target_schema)
            check_filter(source_where, row.label)
            check_filter(target_where, row.label, placeholders=True)
        except ValueError as exc:
            log.warning("Filter not applied, the table is loaded in full: %s: %s", row.label, exc)
            continue
        if source_where or target_where:
            found[table] = (source_where, target_where)
    log.info("Filter files: %d table filter(s) read, from %d workbook row(s)", len(found), len(rows))
    return found


if __name__ == "__main__":
    # Self-check, in the style of yaml_builder.py. Runs with no database and no workbook.
    args = ("postgres", "public", "DEV_EDGE_BRONZE", "STOREDGE_FMS_PUBLIC")

    # One join: the joined table becomes a sub-select on the foreign key. Own conditions come first.
    src, tgt = convert(
        "select count(*) from  general_ledger_line_items  j\njoin  facilities f on j.facility_id=f.id \n"
        "where f.company_id in (137,9088,501) and j.created_at >=  '2024-08-01';", *args)
    assert src == ("created_at >= '2024-08-01' AND facility_id IN "
                   '(SELECT id FROM "public"."facilities" WHERE company_id in (137,9088,501))'), src
    assert tgt == ("created_at >= '2024-08-01' AND facility_id IN "
                   '(SELECT id FROM DEV_EDGE_BRONZE."STOREDGE_FMS_PUBLIC"."FACILITIES" '
                   "WHERE company_id in (137,9088,501) AND _FIVETRAN_ACTIVE = TRUE)"), tgt

    # Chained joins: phone_numbers -> tenants -> facilities. Each level is a sub-select inside the one above.
    src, _ = convert(
        "select count(*) from phone_numbers  j join  tenants  t on j.owner_id=t.id join  facilities  f on t.facility_id=f.id "
        "where  j.owner_type in ( 'Tenant') and f.company_id in (137,9088,501)", *args)
    assert src == ("owner_type in ( 'Tenant') AND owner_id IN (SELECT id FROM \"public\".\"tenants\" WHERE "
                   'facility_id IN (SELECT id FROM "public"."facilities" WHERE company_id in (137,9088,501)))'), src

    # A plain condition on one table, and no filter at all.
    assert convert("select count(*) from account_balance_items   \nwhere   created_at >=  '2026-08-01';", *args)[0] \
        == "created_at >= '2026-08-01'"
    assert convert("select count(*) from invoiceable_items", *args) == (None, None)

    # A join with no condition on the joined table keeps the rows that have a parent (inner join).
    src, _ = convert("select count(*) from discount_plan_ledger_instances_line_items j join discount_plan_ledger_instances ss "
                     "on j.discount_plan_ledger_instance_id=ss.id where created_at >=  '2026-08-01';", *args)
    assert src == ("created_at >= '2026-08-01' AND discount_plan_ledger_instance_id IN "
                   '(SELECT id FROM "public"."discount_plan_ledger_instances")'), src

    # A join on a named key (SiteLink: Sites by SiteID, Owners by OwnerID). Column case is kept.
    src, tgt = convert("select count(*) from Units t join Sites s on t.SiteID = s.SiteID join Owners o on s.OwnerID = o.OwnerID "
                       "where o.sCorpCode in ('SLQA')", "mssql", "dbo", "{env}_SITELINK_BRONZE", "AWSDEV_DEVT5000_DBO")
    assert src == ("SiteID IN (SELECT SiteID FROM [dbo].[Sites] WHERE "
                   "OwnerID IN (SELECT OwnerID FROM [dbo].[Owners] WHERE sCorpCode in ('SLQA')))"), src
    assert tgt == ('SiteID IN (SELECT SiteID FROM {env}_SITELINK_BRONZE."AWSDEV_DEVT5000_DBO"."SITES" WHERE '
                   'OwnerID IN (SELECT OwnerID FROM {env}_SITELINK_BRONZE."AWSDEV_DEVT5000_DBO"."OWNERS" '
                   "WHERE sCorpCode in ('SLQA') AND _FIVETRAN_ACTIVE = TRUE) AND _FIVETRAN_ACTIVE = TRUE)"), tgt

    # Refused: each one names the reason. The table is then loaded in full.
    for bad in [
        "select count(*) from transactions  j join facilities f on j.facility_id=f.id where f.company_id in (137) "
        "and j.created_at >=  2025-08-01';",                                           # unbalanced quote (a typo in the workbook)
        "select count(*) from events j join facilities f on j.facility_id=f.id where f.company_id = 1 or j.created_at > '2025-01-01'",
        "select count(*) from events j left join facilities f on j.facility_id=f.id where f.company_id = 1",
        "select count(*) from events j join facilities f on j.facility_id=f.id where j.created_at > f.created_at",
        "select count(*) from events j join facilities f on j.facility_id=j.id where f.company_id = 1",  # does not reach f
        "delete from events where created_at > '2025-01-01'",
    ]:
        try:
            convert(bad, *args)
        except ValueError:
            continue
        raise AssertionError(f"must be refused: {bad}")

    # A comment marker is read through, and load() refuses it with check_filter(), as for any rules-workbook filter.
    try:
        check_filter(convert("select count(*) from events j where j.created_at > '2025-01-01' -- note", *args)[0], "x")
    except ValueError:
        pass
    else:
        raise AssertionError("-- must be refused by check_filter")

    # Literal text is not read as SQL: an 'or' or a dot inside quotes is kept.
    assert convert("select count(*) from events where status = 'x or y.z'", *args)[0] == "status = 'x or y.z'"

    # A workbook cell has no 'select count(*) from'; the framework adds it. A cell that starts with 'select' is kept.
    assert _count_statement("events j\njoin facilities f on j.facility_id=f.id\nwhere f.company_id in (137)", "events") \
        == "select count(*) from events j\njoin facilities f on j.facility_id=f.id\nwhere f.company_id in (137)"
    assert _count_statement("select count(*) from events", "events") == "select count(*) from events"

    # A cell with only conditions (no table line, no join) is about the row's table, with or without a leading 'where'.
    assert _count_statement("created_at >= '2026-08-01'", "account_balance_items") \
        == "select count(*) from account_balance_items where created_at >= '2026-08-01'"
    assert _count_statement("where created_at >= '2026-08-01'", "account_balance_items") \
        == "select count(*) from account_balance_items where created_at >= '2026-08-01'"
    assert convert(_count_statement("created_at >= '2026-08-01'", "account_balance_items"), *args)[0] == "created_at >= '2026-08-01'"
    print("filter_files self-check passed")
