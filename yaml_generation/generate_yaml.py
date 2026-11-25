"""YAML generation for the Storable automation framework. main.py calls run_for_validation() before the validators run.

It is not a command of its own. main.py runs it on every bronze run (--yaml_generation is yes by default; no only creates a
missing YAML). The filter workbooks (filter_files.py) are the one source of truth for where each table lives: one row per
table names its application, source schema, Snowflake database and Snowflake schema. Nothing about a location is passed on
the command line or kept in db_rules.py. The tables are generated one location at a time, so one run can hold the tables of
several applications on the same engine. For each table, in the same order as the guide (section 5):
  1. Source and target. The source engine is the layer's (postgres or mssql, from --layer_type); the schemas and the
     Snowflake database come from the table's workbook row. The target is Snowflake.
  2. Read the columns of the source table and of the Snowflake table, and the source primary key.
  3. Reconcile the two column lists: normalise the names, match them exactly, and apply the exclusions
     (db_rules.RULES exclude_columns for the engine, and the row's exclude_columns cell). No fuzzy matching, no schema or
     type check. A column on one side only is kept: the other side holds a token, so its rows differ and the validation
     reports it.
  4. Apply the row filters: the row's source filter cell on the source, its target filter cell on the target. An
     incremental run also gets the window, on the row's source_incremental_column on the source and its
     target_incremental_column on the target (either blank FAILS the table's incremental data and count). The YAML holds :start_time and
     :end_time only; the validators bind them at run time from the control table and Fivetran. A window column that is
     not on both sides is still written, by name, with a WARNING: the validation query fails on the side that lacks it.
  5. Check the YAML against this structure. Write it only when the content differs: NEW (no file), UPDATED (the
     structure or a rule changed) or UNCHANGED (left alone). So an out-of-date YAML is rewritten before it is used.

The workbook writes the Snowflake database as {env}_EDGE_BRONZE. It is read for this run's --environment (DEV_EDGE_BRONZE
for dev) and written as it is. The validators fill in {env} for their own run (DEV, STG, QAT or PRD), so one YAML serves
every environment.

What it writes (the same tree main.py reads; the guide explains why run_type is in the path)
  config/<run_type>/<layer>/data_validation/<table>.yaml     one file per table
  config/<run_type>/<layer>/count_validation/<layer>.yaml   one file for all tables
  config/<run_type>/<layer>/schema_validation/schema_validation.yaml    one file for all tables (--schema_validation yes)

The schema file holds the table names (no exclusions: the validator reads db_rules and the master workbook itself),
the catalogue queries (db_rules.RULES schema_query, filled from the workbook row), and the type mapping from the
workbook's '<engine> schema mapping' sheet. A source type with no mapping row FAILS that table's schema. The Snowflake
database keeps {env} ({env}_EDGE_BRONZE); the validator fills it and {table_name}. The validator reads one query per file,
so the tables of a second location in the workbook FAIL their schema.

Nothing is ever deleted. A named table with no row in the filter workbooks is FAILED (its schema is not known). A table that
is not in the source or not in Snowflake is FAILED, and the YAML it already
has is left as it is. A table with no usable key has its data FAILED, with the reason in the log, and its count still
written: the count needs no key. A table has no usable key when it has no primary key (and no key_columns in the table
rules), or when a key column is not on both sides of the comparison. A named table whose YAML is missing is created
(main.py asks for it with create_only).

Statuses per table and kind: NEW, UPDATED, UNCHANGED (ready to validate), PRESENT (ready: the YAML was already there and
create_only left it alone), FAILED (an error, not validated, nothing written for that table or kind, and the run exits 1).
"""

import sys
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

# Make the framework root importable (db/, utils/) however this file is started.
FRAMEWORK_ROOT = Path(__file__).resolve().parents[1]
if str(FRAMEWORK_ROOT) not in sys.path:
    sys.path.insert(0, str(FRAMEWORK_ROOT))

from db.factory import get_database  # noqa: E402
from yaml_generation import db_rules, filter_files  # noqa: E402
from yaml_generation.db_rules import RULES  # noqa: E402
from yaml_generation.yaml_builder import (  # noqa: E402
    ENV_PLACEHOLDER,
    FIVETRAN_FILTER,
    GENERATED_MARK,
    Filters,
    TableSpec,
    build_count_entry,
    build_data_entry,
    data_header,
    count_table_names,
    data_file_path,
    generated_tables,
    incremental_window,
    key_expression,
    missing_key_columns,
    normalize_name,
    other_environment_tables,
    plain_text_columns,
    reconcile,
    schema_table_names,
    unmapped_types,
    upsert_count_file,
    write_data_file,
    write_schema_file,
)

ENV_TOKENS = {"dev": "DEV", "stg": "STG", "qat": "QAT", "prod": "PRD", "local": "DEV"}  # local is DEV, as in the validators
READY_STATUSES = {"NEW", "UPDATED", "UNCHANGED", "PRESENT"}


def resolve_args(args):
    """Check the run's flags, and work out the Snowflake name to read. Raises ValueError with the reason.

    The location is never a default: it is the table's filter workbook row (filter_files.Location).
      args.source_database, args.source_schema, args.target_schema  as the row writes them
      args.target_template  the Snowflake database as written to the YAML, e.g. {env}_EDGE_BRONZE
      args.target_database  the name this run reads the catalogue from: {env} filled in for --environment (DEV_EDGE_BRONZE
                            for dev). filter_files.read() refuses a row without {env}.
    run_for_validation() calls this once for the flags (no location yet: target_template None), then once per location.
    """
    if args.data_validation == "no" and args.count_validation == "no" and args.schema_validation == "no":
        raise ValueError("nothing to generate: data, count and schema validation are all no")
    if "all" in args.tables and len(args.tables) > 1:
        raise ValueError("'all' cannot be combined with table names")
    token = ENV_TOKENS.get(args.environment)
    if token is None:
        raise ValueError(f"unknown --environment {args.environment}: expected one of {', '.join(ENV_TOKENS)}")
    args.target_database = args.target_template.replace(ENV_PLACEHOLDER, token) if args.target_template else None
    # CHANGE: YAML generation. {env} on the source side too (e.g. {env}_NEWBOOK_AU_BRONZE). args.source_database stays as the
    # workbook writes it, for the YAML; source_database_live is the name this run reads; env_token fills the schema query.
    args.source_database_live = args.source_database.replace(ENV_PLACEHOLDER, token) if args.source_database else None
    args.env_token = token
    return args


def generate(args, source_db, target_db, config_dir, global_excluded, table_rules, log, stamp, file_filters=None,
             create_only=False, schema_mapping=None, incremental_columns=None, target_tables=None):
    """Write the YAML for the tables of one location. Returns results.

    args          resolve_args() output for one location: args.tables are table names (run_for_validation() turns 'all'
                  into the workbook's names), and the schemas and Snowflake database are those of their workbook row.
    results       (table, kind, status, detail). status is one of the statuses in the module docstring.
    One table failing does not stop the others. Nothing is deleted. A table that is not in the source or not in Snowflake
    is FAILED, and the YAML it already has is left as it is; main.py does not validate it. A table with no usable key
    (no primary key, or a key column not on both sides) has its data FAILED and its count written. Only a table whose
    columns cannot be compared has nothing written.
    file_filters        {table: (source_where, target_where)} from the filter workbook, or None when no filter file is used.
                        The source cell's condition and the target cell's.
    create_only         True for a run without --yaml_generation: a YAML that is already there is left alone (PRESENT),
                        and only a missing one is written.
    schema_mapping      (datatype_map, precision_checks) from filter_files.schema_mapping() to write the schema file for
                        these tables, or None for no schema file.
    table_rules         {table: filter_files.TableRule}: the row's key_columns and exclude_columns cells.
    incremental_columns {table: (source column, target column)} from the master workbook's source_incremental_column and
                        target_incremental_column cells (filter_files.Row). Read on an incremental run only. Either one
                        blank: the table's data and count FAIL.
    target_tables       {table: target name} from the master workbook's Target Table Name cells (filter_files.Row). A table
                        not in it is looked up in the target under its source name. CHANGE: YAML generation (24/11/2026).
    """
    source = RULES[args.source_type]
    # CHANGE: YAML generation. The target engine is the workbook row's (args.target_type, Snowflake unless the row names another).
    # Only Snowflake reads its catalogue through a database prefix and keeps the Fivetran rule.
    target_type = getattr(args, "target_type", "snowflake")
    snow = RULES[target_type]
    target_label = snow["label"]
    prefix = f"{args.target_database}." if target_type == "snowflake" else ""
    # CHANGE: YAML generation. A Snowflake source (Newbook) reads its catalogue through its own database, not the connection's default
    # (DEV_EDGE_BRONZE in creds), which would list no Newbook table. Rules flag catalogue_prefix.
    source_prefix = f"{args.source_database_live}." if source.get("catalogue_prefix") else ""
    layer = filter_files.LAYER_OF[args.source_type]  # CHANGE: YAML generation . Layer from filter_files.LAYERS
    want_data = args.data_validation == "yes"
    want_count = args.count_validation == "yes"

    # An empty listing would make every table look missing. Refused before anything is read or written.
    if not db_rules.list_tables(source_db, args.source_schema, prefix=source_prefix, rules=source):
        raise ValueError(f"no tables visible in source schema {args.source_schema}: check source_schema in the filter "
                         f"workbook and the role's access (nothing was changed)")
    if not db_rules.list_tables(target_db, args.target_schema, prefix=prefix, rules=snow):
        raise ValueError(f"no tables visible in {target_label} schema {args.target_database}.{args.target_schema}: check "
                         f"target_database and target_schema in the filter workbook, and the role's access (nothing was changed)")
    results = []
    count_entries = {}
    counted = count_table_names(config_dir, layer) if create_only else set()
    want_schema = schema_mapping is not None
    schema_entries = {}
    if want_schema:
        datatype_map, precision_checks = schema_mapping
        schema_sql = db_rules.schema_query(source, args.source_schema, args.source_database)
        sheet = filter_files.SCHEMA_SHEETS[args.source_type]
        in_schema_file = {name.lower() for name in schema_table_names(config_dir)} if create_only else set()

    rules_by_name = {name.lower(): rule for name, rule in table_rules.items()}
    for table in args.tables:
        # CHANGE: YAML generation . A rules row matches its table without regard to case (Newbook names are upper case).
        rule = rules_by_name.get(table.lower(), filter_files.TableRule())
        file_source, file_target = (file_filters or {}).get(table, (None, None))
        filters = Filters(source_where=file_source, target_where=file_target,
                          target_active=FIVETRAN_FILTER if target_type == "snowflake" else None,
                          # CHANGE: YAML generation . A Fivetran source (Newbook) keeps only its active rows too.
                          source_active=FIVETRAN_FILTER if source.get("fivetran_source") else None)
        if file_filters is not None and table not in file_filters:
            log.info("%s: no filter in the filter workbook, so the whole table is compared", table)
        data_wanted = want_data and not (create_only and data_file_path(config_dir, table).exists())
        count_wanted = want_count and not (create_only and table in counted)
        schema_wanted = want_schema and not (create_only and table.lower() in in_schema_file)
        for kind, asked, wanted in (("data_validation", want_data, data_wanted),
                                    ("count_validation", want_count, count_wanted),
                                    ("schema_validation", want_schema, schema_wanted)):
            if asked and not wanted:
                results.append((table, kind, "PRESENT", "YAML already there; not changed"))
        if not (data_wanted or count_wanted or schema_wanted):
            continue
        spec = TableSpec(args.source_type, args.source_schema, table, args.target_template, args.target_schema,
                         source_database=args.source_database, target_type=target_type,
                         target_table_name=(target_tables or {}).get(table))
        try:
            source_columns = db_rules.list_columns(source_db, args.source_schema, table, prefix=source_prefix, rules=source)
            target_columns = db_rules.list_columns(target_db, spec.target_schema, spec.target_table, prefix=prefix, rules=snow)
            if not source_columns:
                reason = f"not in source schema {args.source_schema}, or not visible to this login (it needs read access)"
                results.append((table, "table", "FAILED", f"{reason}; nothing was changed"))
                continue
            if not target_columns:
                reason = (f"not in {target_label} {args.target_database}.{spec.target_schema}, or not visible to this role "
                          f"(it needs read access)")
                results.append((table, "table", "FAILED", f"{reason}; nothing was changed"))
                continue

            rec = reconcile(source_columns, target_columns, global_excluded, rule.exclude, source)
            only_source = [r[0] for r in rec.rows if r[2] == "MISSING_IN_TARGET"]
            only_target = [r[1] for r in rec.rows if r[2] == "EXTRA_IN_TARGET"]
            log.info("%s: %d columns compared; %d only in source; %d only in %s; %d excluded",
                     table, len(rec.pairs), len(only_source), len(only_target), target_label,
                     sum(1 for r in rec.rows if r[2].startswith("EXCLUDED")))
            if only_source or only_target:
                log.warning("%s: column(s) on one side only, compared as missing (every row will differ): %s",
                            table, ", ".join(only_source + only_target))
            plain = plain_text_columns(rec.pairs, source)
            if plain:
                log.warning("%s: %d column(s) compared as plain text, may FAIL on format: %s",
                            table, len(plain), ", ".join(plain))

            if schema_wanted:
                # The type check runs the schema file's own sourcequery, so it sees the types the validator will look up.
                # Excluded columns are not checked: the validator leaves them out, from the same db_rules and master workbook.
                excluded = {s for s, _, status, _ in rec.rows if status.startswith("EXCLUDED") and s}
                columns = [(row[1], row[2]) for row in source_db.execute_query(schema_sql.format(table_name=table, env=args.env_token)).values.tolist()]
                gaps = unmapped_types(columns, excluded, datatype_map)
                if not columns:
                    reason = (f"the schema query (information_schema) returns no column for {args.source_schema}.{table}, so "
                              f"the validator would not find it; check the role's access to the table")
                elif gaps:
                    reason = (f"source type(s) with no row in the '{sheet}' sheet: {', '.join(gaps)}. Add a row for each "
                              f"missing type (source_data_type and its Snowflake target_data_type) and run again")
                else:
                    reason = None
                if reason:
                    log.error("%s: schema YAML not written: %s", table, reason)
                    results.append((table, "schema_validation", "FAILED", f"{reason}; nothing was changed"))
                else:
                    schema_entries[table] = {}

            if args.run_type == "incremental" and (data_wanted or count_wanted):
                # The window is the validators' (CSV and Fivetran at run time). Always written: a side that lacks the column
                # gets a WARNING, and the query fails there.
                # A blank cell FAILS the table's data and count, with no guessed updated_at: the validators' freshness check
                # reads the same cells and would fail on them anyway.
                source_column, target_column = (incremental_columns or {}).get(table, ("", ""))
                blank = [header for header, value in zip(filter_files.INCREMENTAL_HEADERS, (source_column, target_column))
                         if not value]
                if blank:
                    reason = (f"no {' or '.join(blank)} in its row of {filter_files.WORKBOOKS[args.source_type]}, so the "
                              f"incremental window has no column; fill the cell (e.g. updated_at) and run again; nothing was changed")
                    log.error("%s: incremental YAML not written: %s", table, reason)
                    results += [(table, kind, "FAILED", reason)
                                for kind, wanted in (("data_validation", data_wanted), ("count_validation", count_wanted)) if wanted]
                    continue
                filters.source_window, filters.target_window, missing = incremental_window(rec.pairs, source_column, source, snow,
                                                                                           target_column)
                where = {"source": source["label"], "target": target_label}
                for side in missing:
                    log.warning("%s: incremental column %s is not compared on the %s side (missing or excluded); the window "
                                "is written by name, so the validation query fails there if the column is missing",
                                table, source_column if side == "source" else target_column, where[side])

            if data_wanted:
                # CHANGE: YAML generation. The source key is read in the source's own database (source_prefix, Newbook).
                # Excluded columns are dropped from a key read from the database: Fivetran declares ID + _FIVETRAN_START, and
                # _FIVETRAN_START is never compared, so the key is ID. A key named in the row's key_columns cell is used as written.
                key_columns = rule.key_columns
                if not key_columns:
                    read_key = db_rules.primary_key_columns(source_db, args.source_schema, table, source, prefix=source_prefix)
                    dropped = {normalize_name(k) for k in read_key} & (global_excluded | rule.exclude)
                    # A key that is all excluded is kept as read, so the failure below names the excluded column.
                    key_columns = [k for k in read_key if normalize_name(k) not in dropped] or read_key
                    if dropped and key_columns != read_key:
                        log.info("%s: key %s from the source; excluded column(s) left out of it: %s",
                                 table, ", ".join(read_key), ", ".join(sorted(dropped)))
                not_on_both = missing_key_columns(key_columns, rec.pairs)
                if rec.problems:
                    reason = "columns cannot be compared: " + "; ".join(rec.problems)
                elif not key_columns:
                    reason = (f"unable to find the primary key of {args.source_schema}.{table} in the source; name it in "
                              f"the key_columns cell of its row in {filter_files.WORKBOOKS[args.source_type]}, or add the key to the source")
                elif not_on_both:
                    reason = "key column(s) not on both sides of the comparison: " + ", ".join(not_on_both)
                else:
                    reason = None
                if reason:
                    log.error("%s: data YAML not written: %s", table, reason)
                    results.append((table, "data_validation", "FAILED", f"{reason}; nothing was changed"))
                    if rec.problems:
                        continue  # a table whose columns cannot be compared is not counted either
                else:
                    entry = build_data_entry(spec, rec.pairs, key_columns, filters)
                    header = data_header(spec, key_expression(key_columns), rec, filters, stamp)
                    path, status = write_data_file(config_dir, spec, entry, header)
                    results.append((table, "data_validation", status, str(path)))

            # Added last. A table with no usable key still gets its count; one whose columns cannot be compared does not.
            if count_wanted:
                count_entries[table] = build_count_entry(spec, filters)
        except Exception:  # one bad table must not stop the batch
            log.error("%s: generation failed", table, exc_info=True)
            results.append((table, "table", "FAILED", "unexpected error, see the log above"))

    if count_entries:
        others = [t for t in other_environment_tables(config_dir, layer, args.target_template) if t not in count_entries]
        if others:
            log.warning("The count file also holds tables for another environment (not %s): %s. "
                        "The framework reads one file per layer, so these tables are not for this run.",
                        args.target_database, ", ".join(others))
        try:
            path, statuses = upsert_count_file(config_dir, layer, count_entries)
            results.extend((table, "count_validation", statuses[table], str(path)) for table in count_entries)
        except Exception:
            log.error("count file update failed", exc_info=True)
            results.extend((table, "count_validation", "FAILED", "count file could not be updated")
                           for table in count_entries)
    if schema_entries:
        # CHANGE: YAML generation. The maps are written as the workbook gives them (lower case). The catalogue's own
        # spelling (Newbook's TIMESTAMP_NTZ) is no longer added: the validator lower-cases the type, so it was a second, unused entry.
        shared = {"source": args.source_type, "sourcequery": schema_sql, "target": target_type,
                  # The {env} placeholder is kept: the validator fills it for its run, so one file serves every environment.
                  "targetquery": db_rules.schema_query(snow, args.target_schema, args.target_template),
                  "datatype_map": datatype_map, "precision_checks": precision_checks}
        header = [f"{GENERATED_MARK} on {stamp} from {filter_files.WORKBOOKS[args.source_type]} (locations; '{sheet}' for "
                  f"the types). Do not hand-edit; main.py --schema_validation yes rewrites it when they change."]
        try:
            path, statuses = write_schema_file(config_dir, schema_entries, shared, header, create_only=create_only)
            results.extend((table, "schema_validation", statuses[table], str(path)) for table in schema_entries)
        except Exception:
            log.error("schema file update failed", exc_info=True)
            results.extend((table, "schema_validation", "FAILED", "schema file could not be updated")
                           for table in schema_entries)
    return results


def config_dir_for(layer_type: str, run_type: str) -> Path:
    """The folder the YAML for one layer and run type is written to, and read from by main.py."""
    return FRAMEWORK_ROOT / "config" / run_type / layer_type


def tables_missing_yaml(layer_type: str, run_type: str, tables, data_validation: str, count_validation: str,
                        schema_validation: str = "no") -> list:
    """The named tables with no YAML for a validation this run asks for. Only the files are checked, no database is read.

    'all' is not checked here: its list comes from the source, so generate() reads it.
    """
    config_dir = config_dir_for(layer_type, run_type)
    counted = count_table_names(config_dir, layer_type)
    in_schema = {name.lower() for name in schema_table_names(config_dir)}
    return [table for table in tables
            if (data_validation == "yes" and not data_file_path(config_dir, table).exists())
            or (count_validation == "yes" and table not in counted)
            or (schema_validation == "yes" and str(table).lower() not in in_schema)]


def workbook_spelling(layer_type, run_type, tables) -> list:
    """The table names as the layer's filter workbook writes them (case is ignored), for main.py before anything runs.

    'all', a name not in the workbook, and a workbook that cannot be read leave the names as typed: run_for_validation()
    reports those.
    """
    try:
        return filter_files.spelled(tables, filter_files.read(filter_files.LAYERS[layer_type], run_type))
    except (ValueError, KeyError):  # KeyError: a layer with no engine in db_rules.RULES
        return list(tables)


def run_for_validation(layer_type, tables, environment, run_type, data_validation, count_validation,
                       use_filter_files=True, create_only=False, log=None, schema_validation="no"):
    """Check and update the YAML for one main.py run. Returns (results, ready_tables).

    Where each table lives comes from its row in the filter workbooks (filter_files.read()), not from arguments. The tables
    are grouped by that location (application, schemas, Snowflake database) and generated one group at a time.
    use_filter_files  True applies the rows' filter cells; False reads only the locations and compares whole tables.
    schema_validation 'yes' also writes the schema file, from the workbook's type mapping sheet. Its location is the one of
                      the workbook's first row; a table at another location is FAILED for schema (one query per file).

    results       (table, kind, status, detail) for every table and kind, as in generate(). The per-column comparison
                  is written to the log only. A named table with no workbook row is FAILED. A location whose schema lists no
                  table FAILS its own tables, and the other locations go on.
    ready_tables  the tables that can be validated now: no table-level FAILED (no workbook row, not in the source or
                  Snowflake, or an error), and at least one result NEW, UPDATED, UNCHANGED or PRESENT. A FAILED data result
                  does not block the count. The validators run on these tables only.
    Raises ValueError, with nothing written, when the run cannot start: a bad argument, a bad workbook row, or a
    connection that cannot be opened. main.py reports it and does not validate.
    """
    source_type = filter_files.LAYERS.get(layer_type)  # CHANGE: YAML generation . The engine from filter_files.LAYERS
    if source_type not in db_rules.SOURCE_TYPES:
        raise ValueError(f"--layer_type {layer_type} has no YAML generation: it is not in filter_files.LAYERS, "
                         f"or db_rules.RULES has no engine '{source_type}'")
    args = resolve_args(SimpleNamespace(
        source_type=source_type, tables=tables, environment=environment, data_validation=data_validation,
        count_validation=count_validation, schema_validation=schema_validation, run_type=run_type, source_schema=None,
        source_database=None, target_template=None, target_schema=None))
    label = RULES[source_type]["label"]
    layer = filter_files.LAYER_OF[source_type]  # CHANGE: YAML generation . Layer from filter_files.LAYERS
    config_dir = config_dir_for(layer, run_type)

    # The workbooks first: they say where every table lives. A bad row stops the run before anything is read or written.
    try:
        rows = filter_files.read(source_type, run_type, log=log)
        schema_mapping = filter_files.schema_mapping(source_type, log=log) if schema_validation == "yes" else None
    except ValueError as exc:
        raise ValueError(f"Filter workbook problem: {exc}") from exc
    if not rows:
        raise ValueError(f"no table in the filter workbook {filter_files.WORKBOOKS[source_type]}: add one row per table, with "
                         f"source_database {label} and its source and target columns")
    # A name is matched without regard to case, and the workbook's spelling is used from here on (--tables addresses is Addresses).
    requested = list(rows) if "all" in tables else filter_files.spelled(tables, rows)
    results = [(table, "table", "FAILED", f"no row in the filter workbooks for {label}, so its schema is not known; "
                                          f"nothing was changed") for table in requested if table not in rows]
    by_location = {}
    for table in requested:
        if table in rows:
            by_location.setdefault(rows[table].location, []).append(table)

    # CHANGE: YAML generation (25/11/2026). Keys and exclusions come from the master rows (key_columns, exclude_columns) and
    # db_rules.RULES; the settings folder (dev_rules.xlsx, exclusions.yaml) is gone.
    table_rules = {where: {table: rows[table].rule for table in names} for where, names in by_location.items()}
    global_excluded = db_rules.excluded_columns(source_type)
    log.info("Exclusions for %s and snowflake: %d", source_type, len(global_excluded))

    try:
        # The same cached connections the validators use. They are closed by main.py, not here.
        source_db = get_database(source_type, str(FRAMEWORK_ROOT), environment)
        # CHANGE: YAML generation. One connection per target engine the rows name (Snowflake, or e.g. mysql_crm).
        target_dbs = {kind: get_database(kind, str(FRAMEWORK_ROOT), environment) for kind in {w.target_type for w in by_location}}
    except (FileNotFoundError, KeyError) as exc:
        # Missing creds/<env>.yaml (FileNotFoundError) or a missing block in it, e.g. no 'snowflake' key (KeyError).
        raise ValueError(f"Cannot open the connections for --environment {environment}: {exc}. "
                         f"Check creds/{environment}.yaml.") from exc

    # Row filters from the same rows, for this run type. Only the requested tables, so the log names no other table.
    file_filters = (filter_files.filters({table: rows[table] for group in by_location.values() for table in group}, log=log)
                    if use_filter_files else None)

    stamp = datetime.now().isoformat(timespec="seconds")
    log.info("YAML generation into %s", config_dir)
    # The schema file has one sourcequery and one targetquery: the location of the workbook's first row (validator change V7
    # would allow more).
    schema_home = next(iter(rows.values())).location
    for where, names in by_location.items():
        if schema_mapping is not None and where != schema_home:
            reason = (f"the schema file holds one location ({schema_home.source_schema} -> {schema_home.target_database}."
                      f"{schema_home.target_schema}), and this table is at {where.source_schema} -> {where.target_database}."
                      f"{where.target_schema}; nothing was changed")
            log.error("%s: schema YAML not written for %s: %s", where.application, ", ".join(names), reason)
            results += [(table, "schema_validation", "FAILED", reason) for table in names]
        group = resolve_args(SimpleNamespace(**{**vars(args), "tables": names, "source_schema": where.source_schema,
                                                "source_database": where.source_database,
                                                "target_template": where.target_database, "target_schema": where.target_schema,
                                                "target_type": where.target_type}))
        log.info("%s: %d table(s), %s %s.%s -> %s %s.%s, %d table rule(s)", where.application, len(names), label,
                 where.source_database, where.source_schema, RULES[where.target_type]["label"], group.target_database,
                 where.target_schema, len(table_rules[where]))
        try:
            results += generate(group, source_db, target_dbs[where.target_type], config_dir, global_excluded, table_rules[where], log, stamp,
                                file_filters=file_filters, create_only=create_only,
                                schema_mapping=schema_mapping if where == schema_home else None,
                                incremental_columns={table: (rows[table].source_incremental_column,
                                                             rows[table].target_incremental_column) for table in names},
                                target_tables={table: rows[table].target_table for table in names})
        except ValueError as exc:
            # A schema that lists no table. Its tables FAIL; the next location still runs.
            log.error("%s: %s", where.application, exc)
            results += [(table, "table", "FAILED", str(exc)) for table in names]
        if "all" in tables:
            # A YAML this generator wrote for a table that is no longer in the workbooks is left as it is, and not validated.
            left = sorted(generated_tables(config_dir, layer, where.target_database, where.source_schema) - set(rows))
            if left:
                log.warning("Generated YAML for %s is not in the filter workbooks for %s. It is left unchanged and not validated.",
                            ", ".join(left), where.application)
    if schema_mapping is not None and "all" in tables:
        listed = {table.lower() for table in rows}
        left = sorted(table for table in schema_table_names(config_dir) if str(table).lower() not in listed)
        if left:
            log.warning("The schema file holds %s, which are not in the filter workbook. They are left unchanged and not validated.",
                        ", ".join(left))
    for table, kind, status, detail in results:
        log.info("YAML %-9s %-35s %-17s %s", status, table, kind, detail)

    # A table is ready when one of its YAML is ready, and its count is ready if it was asked for: a count file that could
    # not be updated must not be validated against an old count. A data FAILED (no usable key) does not stop the count.
    blocked = {table for table, kind, status, _ in results if kind == "table" and status == "FAILED"}
    handled = list(dict.fromkeys(table for table, _, _, _ in results))
    ready_tables = [table for table in handled
                    if table not in blocked
                    and any(t == table and s in READY_STATUSES for t, _, s, _ in results)
                    and not any(t == table and k == "count_validation" and s not in READY_STATUSES for t, k, s, _ in results)]
    return results, ready_tables


def report_failures(results, outputpaths, run_id, run_at, layer_type, log):
    """CHANGE: YAML generation . Each FAILED result goes into the report of its validation, as a FAIL row with the
    reason, so the report shows what the console logged (for example a type with no mapping row, or no primary key).

    results      (table, kind, status, detail), as run_for_validation() returns them.
    outputpaths  main.py's {validation: folder}. A kind with no folder (not asked for this run) is not written.
    The rows use the validators' own writers: schema_validation_result_summary.xlsx, data_validation_result_summary.xlsx
    (and <table>_result.xlsx), and count_validation_summary.csv. A table-level FAILED (not in the source or Snowflake, no
    workbook row) is written to every validation of the run.
    """
    from utils.utility import create_summary, create_summary_schema
    from validators.count_validation import create_summary_count
    writers = {"schema_validation": create_summary_schema, "count_validation": create_summary_count,
               "data_validation": create_summary}
    source = filter_files.LAYERS.get(layer_type)
    for table, kind, status, detail in results:
        if status != "FAILED":
            continue
        for validation in (list(outputpaths) if kind == "table" else [kind]):
            if validation not in writers or validation not in outputpaths:
                continue
            try:
                writers[validation](run_at, run_id, validation, table, source, None, None, "FAIL", outputpaths[validation],
                                    error_message=f"YAML generation: {detail}", layer_type=layer_type)
            except Exception:  # the report row is extra; it must not stop the run
                log.warning("%s: the YAML generation failure could not be written to the %s report", table, validation,
                            exc_info=True)
