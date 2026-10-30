"""YAML generation for the Storable automation framework. main.py calls run_for_validation() before the validators run.

It is not a command of its own. main.py runs it when --yaml_generation yes is given. For each table, in the same
order as the guide (section 5):
  1. Source and target. The source is the layer's database (postgres or mssql, from --layer_type). The target is Snowflake.
  2. Read the columns of the source table and of the Snowflake table, and the source primary key.
  3. Reconcile the two column lists: normalise the names, match them exactly, and apply the exclusions
     (global list and table rules). No fuzzy matching, no schema or type check. A column on one side only is kept:
     the other side holds a token, so its rows differ and the validation reports it.
  4. Apply the row filters from the table rules workbook (settings/). An incremental run also gets the window on the
     incremental column (default updated_at, or incremental_column in the rules workbook). The YAML holds :start_time and
     :end_time only; the validators bind them at run time from the control table and Fivetran. A table without the
     column on both sides is FAILED.
  5. Check the YAML against this structure. Write it only when the content differs: NEW (no file), UPDATED (the
     structure or a rule changed) or UNCHANGED (left alone). So an out-of-date YAML is rewritten before it is used.

The Snowflake database is read for this run's --environment, but written as {env}_EDGE_BRONZE. The validators fill in
{env} for their own run (DEV, STG, QAT or PRD), so one YAML serves every environment.

What it writes (the same tree main.py reads; the guide explains why run_type is in the path)
  config/<run_type>/bronze_<source_type>/data_validation/<table>.yaml     one file per table
  config/<run_type>/bronze_<source_type>/count_validation/bronze_<source_type>.yaml   one file for all tables
  output/yaml_generation/reconciliation_<time>.csv                        one line per column, with its status

Nothing is ever deleted. A table that is not in the source or not in Snowflake is FAILED, and the YAML it already
has is left as it is. A table with no primary key (and no key_columns in the table rules) is FAILED too, with the
reason in the log: nothing is skipped. A named table whose YAML is missing is created (main.py asks for it with create_only).

Statuses per table and kind: NEW, UPDATED, UNCHANGED (ready to validate), PRESENT (ready: the YAML was already there and
create_only left it alone), FAILED (an error, not validated, nothing written for that table, and the run exits 1).
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
from yaml_generation import db_rules, filter_files, settings  # noqa: E402
from yaml_generation.db_rules import RULES  # noqa: E402
from yaml_generation.yaml_builder import (  # noqa: E402
    ENV_PLACEHOLDER,
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
    other_environment_tables,
    plain_text_columns,
    reconcile,
    upsert_count_file,
    write_data_file,
)

ENV_TOKENS = {"dev": "DEV", "stg": "STG", "qat": "QAT", "prod": "PRD", "local": "DEV"}  # local is DEV, as in the validators
OUTPUT_DIR = FRAMEWORK_ROOT / "output" / "yaml_generation"
READY_STATUSES = {"NEW", "UPDATED", "UNCHANGED", "PRESENT"}


def resolve_args(args):
    """Fill in the source and target defaults, and check the combination. Raises ValueError with the reason.

    args.target_database is the Snowflake name this run reads the catalogue from (DEV_EDGE_BRONZE for dev).
    args.target_template is the name written to the YAML ({env}_EDGE_BRONZE), so the YAML serves every environment.
    """
    if args.data_validation == "no" and args.count_validation == "no":
        raise ValueError("nothing to generate: both data and count validation are no")
    if "all" in args.tables and len(args.tables) > 1:
        raise ValueError("'all' cannot be combined with table names")
    source = RULES[args.source_type]
    args.source_schema = args.source_schema or source["default_schema"]
    # --target_schema overrides the default schema for the source (EDGE or SITELINK) when the Snowflake schema is named differently.
    args.target_schema = args.target_schema or source["target_schema"]
    if source["target_system"] is None:
        # Athena and Redshift have no Snowflake name of their own. --target_database gives it, and it is written as it is.
        if args.target_database is None:
            raise ValueError(f"--target_database is required for {source['label']}")
        args.target_template = args.target_database
        return args
    token = ENV_TOKENS.get(args.environment)
    if token is None:
        raise ValueError(f"unknown --environment {args.environment}: expected one of {', '.join(ENV_TOKENS)}")
    expected = f"{token}_{source['target_system']}_BRONZE"
    if args.target_database not in (None, expected):
        raise ValueError(f"--target_database {args.target_database} does not match --environment {args.environment} "
                         f"({expected}). Leave --target_database out: the environment picks the database.")
    args.target_database = expected
    args.target_template = f"{ENV_PLACEHOLDER}_{source['target_system']}_BRONZE"
    return args


def generate(args, source_db, target_db, config_dir, global_excluded, table_rules, log, stamp, file_filters=None,
             create_only=False):
    """Write the YAML for every requested table. Returns (results, reconciliation_rows).

    results             (table, kind, status, detail). status is one of the statuses in the module docstring.
    reconciliation_rows (table, source_column, target_column, status, note), one per column.
    One table failing does not stop the others. Nothing is deleted. A table that is not in the source or not in Snowflake,
    or that has no primary key, is FAILED, and the YAML it already has is left as it is; main.py does not validate it.
    file_filters        {table: (source_where, target_where)} from the filter workbook, or None when no filter file is used.
                        Used for a side the rules workbook does not set.
    create_only         True for a run without --yaml_generation: a YAML that is already there is left alone (PRESENT),
                        and only a missing one is written.
    """
    source = RULES[args.source_type]
    snow = RULES["snowflake"]
    layer = f"bronze_{args.source_type}"
    want_data = args.data_validation == "yes"
    want_count = args.count_validation == "yes"

    # An empty listing would make every table look missing. Refused before anything is read or written.
    schema_tables = db_rules.list_tables(source_db, args.source_schema, rules=source)
    if not schema_tables:
        raise ValueError(f"no tables visible in source schema {args.source_schema}: check --source_schema and the role's "
                         f"access (nothing was changed)")
    tables = schema_tables if "all" in args.tables else args.tables
    if not db_rules.list_tables(target_db, args.target_schema, prefix=f"{args.target_database}."):
        raise ValueError(f"no tables visible in Snowflake schema {args.target_database}.{args.target_schema}: check "
                         f"--target_schema and the role's access (nothing was changed)")
    results, recon_rows = [], []
    count_entries = {}
    counted = count_table_names(config_dir, layer) if create_only else set()

    if "all" in args.tables:
        # A YAML this generator wrote for a table that is no longer in the source is left as it is, and not validated.
        left = sorted(generated_tables(config_dir, layer, args.target_template, args.source_schema) - set(tables))
        if left:
            log.warning("Generated YAML for %s is not in source schema %s. It is left unchanged and not validated.",
                        ", ".join(left), args.source_schema)

    for table in tables:
        rule = table_rules.get(table, settings.TableRule())
        # The rules workbook wins for a side it sets. The filter workbook fills in the other side.
        file_source, file_target = (file_filters or {}).get(table, (None, None))
        filters = Filters(source_where=rule.source_where or file_source, target_where=rule.target_where or file_target)
        if file_filters is not None and table not in file_filters:
            log.info("%s: no filter in the filter workbook, so the whole table is compared", table)
        data_wanted = want_data and not (create_only and data_file_path(config_dir, table).exists())
        count_wanted = want_count and not (create_only and table in counted)
        for kind, asked, wanted in (("data_validation", want_data, data_wanted),
                                    ("count_validation", want_count, count_wanted)):
            if asked and not wanted:
                results.append((table, kind, "PRESENT", "YAML already there; not changed"))
        if not (data_wanted or count_wanted):
            continue
        spec = TableSpec(args.source_type, args.source_schema, table, args.target_template, args.target_schema)
        try:
            source_columns = db_rules.list_columns(source_db, args.source_schema, table, rules=source)
            target_columns = db_rules.list_columns(target_db, spec.target_schema, spec.target_table,
                                                   prefix=f"{args.target_database}.")
            if not source_columns:
                reason = f"not in source schema {args.source_schema}, or not visible to this login (it needs read access)"
                results.append((table, "table", "FAILED", f"{reason}; nothing was changed"))
                recon_rows.append((table, "", "", "TABLE_NOT_IN_SOURCE", reason))
                continue
            if not target_columns:
                reason = (f"not in Snowflake {args.target_database}.{spec.target_schema}, or not visible to this role "
                          f"(it needs read access)")
                results.append((table, "table", "FAILED", f"{reason}; nothing was changed"))
                recon_rows.append((table, "", "", "TABLE_NOT_IN_TARGET", reason))
                continue

            rec = reconcile(source_columns, target_columns, global_excluded, rule.exclude, source)
            recon_rows.extend((table, s, t, status, note) for s, t, status, note in rec.rows)
            only_source = [r[0] for r in rec.rows if r[2] == "MISSING_IN_TARGET"]
            only_target = [r[1] for r in rec.rows if r[2] == "EXTRA_IN_TARGET"]
            log.info("%s: %d columns compared; %d only in source; %d only in Snowflake; %d excluded",
                     table, len(rec.pairs), len(only_source), len(only_target),
                     sum(1 for r in rec.rows if r[2].startswith("EXCLUDED")))
            if only_source or only_target:
                log.warning("%s: column(s) on one side only, compared as missing (every row will differ): %s",
                            table, ", ".join(only_source + only_target))
            plain = plain_text_columns(rec.pairs, source)
            if plain:
                log.warning("%s: %d column(s) compared as plain text, may FAIL on format: %s",
                            table, len(plain), ", ".join(plain))

            if args.run_type == "incremental":
                # The window is the validators' (CSV and Fivetran at run time). Refused, not skipped, when the column is missing.
                column = rule.incremental_column or settings.INCREMENTAL_COLUMN
                window = incremental_window(rec.pairs, column, source)
                if window is None:
                    reason = f"incremental column {column} is not compared on both sides (in source and in Snowflake)"
                    log.error("%s: YAML not written: %s", table, reason)
                    for kind, asked in (("data_validation", data_wanted), ("count_validation", count_wanted)):
                        if asked:
                            results.append((table, kind, "FAILED", f"{reason}; nothing was changed"))
                    continue
                filters.source_window, filters.target_window = window

            if data_wanted:
                key_columns = rule.key_columns or db_rules.primary_key_columns(source_db, args.source_schema, table, source)
                if rec.problems:
                    reason = "columns cannot be compared: " + "; ".join(rec.problems)
                elif not key_columns:
                    reason = (f"unable to find the primary key of {args.source_schema}.{table} in the source; name it in "
                              f"the table rules (key_columns), or add the key to the source")
                elif missing_key_columns(key_columns, rec.pairs):
                    reason = ("key column(s) not in both source and Snowflake: "
                              + ", ".join(missing_key_columns(key_columns, rec.pairs)))
                else:
                    reason = None
                if reason:
                    log.error("%s: data YAML not written: %s", table, reason)
                    results.append((table, "data_validation", "FAILED", f"{reason}; nothing was changed"))
                    continue
                entry = build_data_entry(spec, rec.pairs, key_columns, filters)
                header = data_header(spec, key_expression(key_columns), rec, filters, stamp)
                path, status = write_data_file(config_dir, spec, entry, header)
                results.append((table, "data_validation", status, str(path)))

            # Added last: a table whose data step FAILED is not counted either, so nothing is written for it.
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
    return results, recon_rows


def config_dir_for(layer_type: str, run_type: str) -> Path:
    """The folder the YAML for one layer and run type is written to, and read from by main.py."""
    return FRAMEWORK_ROOT / "config" / run_type / layer_type


def tables_missing_yaml(layer_type: str, run_type: str, tables, data_validation: str, count_validation: str) -> list:
    """The named tables with no YAML for a validation this run asks for. Only the files are checked, no database is read.

    'all' is not checked here: its list comes from the source, so generate() reads it.
    """
    config_dir = config_dir_for(layer_type, run_type)
    counted = count_table_names(config_dir, layer_type)
    return [table for table in tables
            if (data_validation == "yes" and not data_file_path(config_dir, table).exists())
            or (count_validation == "yes" and table not in counted)]


def run_for_validation(layer_type, tables, environment, run_type, data_validation, count_validation,
                       source_schema=None, target_database=None, target_schema=None, rules_file=None,
                       use_filter_files=False, create_only=False, log=None):
    """Check and update the YAML for one main.py run. Returns (results, ready_tables).

    results       (table, kind, status, detail) for every table and kind, as in generate().
    ready_tables  the tables that can be validated now: none FAILED, and at least one of their YAML is NEW, UPDATED
                  or UNCHANGED. The validators run on these tables only.
    Raises ValueError, with nothing written, when the run cannot start: a bad argument, a settings problem, or a
    connection that cannot be opened. main.py reports it and does not validate.
    """
    args = resolve_args(SimpleNamespace(
        source_type=layer_type.replace("bronze_", "", 1), tables=tables, environment=environment,
        data_validation=data_validation, count_validation=count_validation, source_schema=source_schema,
        target_database=target_database, target_schema=target_schema, run_type=run_type))
    layer = f"bronze_{args.source_type}"
    config_dir = config_dir_for(layer, run_type)

    try:
        rules_path = settings.pick_rules_file(rules_file)
        table_rules = settings.load_table_rules(rules_path, args.source_type) if rules_path else {}
    except (FileNotFoundError, ValueError) as exc:
        raise ValueError(f"Settings problem: {exc}") from exc
    global_excluded = settings.load_global_exclusions()
    log.info("Rules workbook: %s (%d table rule(s)); global exclusions: %d",
             rules_path or "none", len(table_rules), len(global_excluded))

    try:
        # The same cached connections the validators use. They are closed by main.py, not here.
        source_db = get_database(args.source_type, str(FRAMEWORK_ROOT), args.environment)
        target_db = get_database("snowflake", str(FRAMEWORK_ROOT), args.environment)
    except (FileNotFoundError, KeyError) as exc:
        # Missing creds/<env>.yaml (FileNotFoundError) or a missing block in it, e.g. no 'snowflake' key (KeyError).
        raise ValueError(f"Cannot open the connections for --environment {args.environment}: {exc}. "
                         f"Check creds/{args.environment}.yaml.") from exc

    # Row filters from yaml_generation/Filter-files-excel/, for this run type. Read only when asked for.
    file_filters = (filter_files.load(args.source_type, args.source_schema, args.target_template, args.target_schema,
                                      run_type, log=log) if use_filter_files else None)

    stamp = datetime.now().isoformat(timespec="seconds")
    log.info("YAML generation into %s", config_dir)
    results, recon_rows = generate(args, source_db, target_db, config_dir, global_excluded,
                                   table_rules, log, stamp, file_filters=file_filters, create_only=create_only)
    report = settings.write_reconciliation(
        OUTPUT_DIR / f"reconciliation_{datetime.now():%Y%m%d_%H%M%S}.csv", stamp, recon_rows)
    log.info("Reconciliation report: %s", report)
    for table, kind, status, detail in results:
        log.info("YAML %-9s %-35s %-17s %s", status, table, kind, detail)

    failed = {table for table, _, status, _ in results if status == "FAILED"}
    handled = list(dict.fromkeys(table for table, _, _, _ in results))
    ready_tables = [table for table in handled
                    if table not in failed
                    and any(t == table and s in READY_STATUSES for t, _, s, _ in results)]
    return results, ready_tables
