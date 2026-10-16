# Storable Data Validation Framework

A command-line framework that validates data migrated between systems (Postgres / MSSQL → Snowflake, plus report-level checks on Snowflake/Athena/Redshift). It runs YAML-defined SQL on a **source** and a **target**, compares the results, and writes PASS/FAIL summaries and mismatch details to Excel/CSV.

## Contents
1. [Architecture](#1-architecture)
2. [Setup](#2-setup)
3. [Commands](#3-commands)
4. [Configuration (YAML)](#4-configuration-yaml)
5. [Output](#5-output)
6. [Exit codes](#6-exit-codes)
---

## 1. Architecture

### Layout

```
Storable_automation_framework/
├── main.py                      # Entry point: parse args → build paths → run validators → exit code
├── utils/
│   └── utility.py               # CLI parsing, run-id, config/output path resolution,
│                                #   logger, summary writers, DataContext dataclass
├── validators/                  # One module per validation type; each exposes validate(ctx)
│   ├── schema_validation.py     #   -> (failure_count, system_error)
│   ├── count_validation.py
│   ├── data_validation.py
│   └── integrity_check.py
├── db/                          # Connector layer
│   ├── base.py                  #   abstract Database: connect / execute_query / close
│   ├── factory.py               #   get_database(type, BASE_DIR, env) – cached per (type, env)
│   └── postgres.py, mssqlserver.py, snowflake.py, redshift.py, athena.py
│   └── ddl/ddl.sql              #   "control" table DDL (not read by the framework at runtime)
├── config/                      # (gitignored) YAML test definitions
│   ├── historical/<layer>/...   #   full-load runs
│   └── incremental/<layer>/...  #   date-ranged runs
├── creds/<env>.yaml             # (gitignored) DB credentials per environment
└── output/                      # (gitignored) results, one folder per run
```

### Execution flow

```
python main.py <args>
   │
   ├─ parse_arguments()                 validates flag combinations (argparse)
   ├─ get_config_output_paths()         resolves YAML files + creates output dirs
   ├─ DataContext(...)                  single object handed to every validator
   │
   ├─ for each enabled validator (schema → count → data [+ integrity]):
   │     validate(ctx)
   │       ├─ load YAML → pick tables
   │       └─ per table / per validation:
   │            get_database(source).execute_query(sourcequery)
   │            get_database(target).execute_query(targetquery)
   │            compare → PASS/FAIL → write summary (+ mismatch file)
   │            any exception → table marked FAIL, error captured, run continues
   │
   ├─ close_all_databases()
   └─ sys.exit(1 if system_error else 0)
```

Connections are cached per `(db_type, environment)` so each database is opened once per run and closed at the end.

### Validation types

| Validator | Flag | Config file(s) | What it does |
|---|---|---|---|
| **Schema** | `--schema_validation yes` | one `schema_validation.yaml` per layer | Compares `information_schema`-style column metadata: columns missing in source/target, data-type mapping (`datatype_map`), datetime precision, integer precision, character length. Target being *wider* than source is a PASS with `warning=Yes`. |
| **Count** | `--count_validation yes` | one `<layer>.yaml` per layer | Runs both queries; each must return a single value named `source_row_count` / `target_row_count`. PASS if equal. |
| **Data** | `--data_validation yes` | one YAML per table | Loads both result sets as strings, indexes on `sourcecolumn`/`targetcolumn` (comma-separated for composite keys), sorts, and does a full `DataFrame.equals()`. On mismatch it writes missing-in-source, missing-in-target and per-column differences. |
| **Integrity** | `--integrity_check yes` | `sanity/<pack>/integrity_check/*.yaml` | Runs one query that is expected to return **0 rows**. Any row returned = FAIL, and the offending rows are saved to CSV. |

"Sum", "avg", "distinct" etc. report tests are not separate code paths – they are `data_validation` entries whose SQL aggregates differently.

### Layers

| `--layer_type` | Supports | Notes |
|---|---|---|
| `bronze_postgres` | schema, count, data | Postgres → Snowflake bronze |
| `bronze_mssql` | schema, count, data | MSSQL → Snowflake bronze |
| `silver` | count, data | Requires `{env}` placeholders in queries |
| `reports` | data only | Requires `--report_pack` |
| `sanity` | integrity (+ data) | Requires `--report_pack` |
| `gold` | – | Accepted by argparse but **not implemented** (see §7) |

Report packs: `emanagement`, `smanagement`, `egrowth`, `sgrowth`, `eperformance`, `sperformance`. Packs with YAML on disk today: `emanagement`, `egrowth`, `eperformance`.

### Supported connectors

`source:` / `target:` in a YAML must be exactly one of `postgres`, `mssql`, `athena`, `snowflake`, `redshift`. Anything else (e.g. `postgresql`, `sqlserver`) raises `ValueError: Unsupported database`.

---

## 2. Setup

```powershell
python -m venv venv
.\venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

Requirements: `pyyaml, pyodbc, psycopg2-binary, pandas, boto3, snowflake-connector-python, redshift_connector, awswrangler, openpyxl, certifi`.
MSSQL also needs the **ODBC Driver 18 for SQL Server** installed on the machine.

### Credentials – `creds/<environment>.yaml`

Expected keys per connector (only the ones your YAMLs use need to be present):

```yaml
postgres:  { dbname, user, password, host, port }
mssql:     { driver, server, database, uid, pwd }
athena:    { profile, aws_region, athena_db, athena_output }
snowflake: { snowflake_account, snowflake_user, snowflake_role, externalbrowser,
             snowflake_database, snowflake_schema, snowflake_warehouse }
redshift:  { redshift_host, redshift_port, redshift_database, redshift_user, redshift_password }
```

- Files on disk today: `dev`, `stg`, `uat`, `prod`, `local`. Note `--environment` accepts `dev, stg, qat, prod, local` – **`qat` has no creds file** and `uat` is not an accepted value.
- Snowflake uses SSO (`externalbrowser`): a browser window opens on first connect.
- Athena uses an AWS CLI profile (`profile:`) – it must be logged in already.
- Never commit `creds/`; it is gitignored.

---

## 3. Commands

### Arguments

| Argument | Required | Values | Notes |
|---|---|---|---|
| `--layer_type` | yes | `bronze_postgres bronze_mssql silver gold reports sanity` | |
| `--tables` | yes | one or more names, or `all` | Names must match keys in the YAML (`all` runs every table) |
| `--schema_validation` | yes | `yes` / `no` | |
| `--count_validation` | yes | `yes` / `no` | |
| `--data_validation` | yes | `yes` / `no` | |
| `--integrity_check` | no (default `no`) | `yes` / `no` | |
| `--environment` | yes | `dev stg qat prod local` | Selects `creds/<env>.yaml` and fills `{env}` in queries |
| `--report_pack` | for `reports`/`sanity` only | see above | Rejected for other layers |
| `--run_type` | no (default `historical`) | `historical` / `incremental` | Picks `config/historical` or `config/incremental` |
| `--from_date`, `--to_date` | only for `incremental` | `YYYY-MM-DD` | Both required for incremental; forbidden for historical |

### Examples

```powershell
# Bronze Postgres – everything, all tables
python main.py --layer_type bronze_postgres --tables all `
  --schema_validation yes --count_validation yes --data_validation yes --environment dev

# Bronze MSSQL – only specific tables, data check only
python main.py --layer_type bronze_mssql --tables Sites Units Tenants `
  --schema_validation no --count_validation no --data_validation yes --environment dev

# Bronze Postgres – schema check only
python main.py --layer_type bronze_postgres --tables all `
  --schema_validation yes --count_validation no --data_validation no --environment dev

# Silver[WIP]
python main.py --layer_type silver --tables all `
  --schema_validation no --count_validation yes --data_validation yes --environment dev

# Reports (data validation only)
python main.py --layer_type reports --report_pack emanagement --tables all `
  --schema_validation no --count_validation no --data_validation yes --environment dev

# Sanity / integrity checks
python main.py --layer_type sanity --report_pack egrowth --tables all `
  --schema_validation no --count_validation no --data_validation no `
  --integrity_check yes --environment dev
```

Use `python main.py -h` for the built-in help.

---

## 4. Configuration (YAML)

Path convention: `config/<run_type>/<layer>/<validation>/...`

| Validation | Path |
|---|---|
| schema | `config/<run_type>/<layer>/schema_validation/schema_validation.yaml` |
| count | `config/<run_type>/<layer>/count_validation/<layer>.yaml` |
| data | `config/<run_type>/<layer>/data_validation/<table>.yaml` |
| reports data | `config/<run_type>/reports/<pack>/data_validation/<table>.yaml` |
| integrity | `config/<run_type>/sanity/<pack>/integrity_check/<test>.yaml` |

**Count / data validation:**

```yaml
tables:
  account_codes:
    validations:
      data_validation:            # key is free text; it becomes validation_performed
        source: postgres
        source_table_name: account_codes
        sourcequery: |
          SELECT ... FROM public.account_codes
        sourcecolumn: id          # key column(s), comma-separated for composite keys
        target: snowflake
        target_table_name: account_codes
        targetquery: |
          SELECT ... FROM {env}_EDGE_BRONZE.STOREDGE_FMS_PUBLIC.ACCOUNT_CODES
        targetcolumn: id
```

Count validation queries must return one column each: `source_row_count` and `target_row_count`.

**Placeholders in queries** (filled by `str.format`): `{env}` → `DEV` / `STG` / `QAT` / `PRD`; `{schema_env}` → `DEV` / `STAGING` / `QAT` / `PRD` (data validation). Because of this, **literal `{` `}` in SQL must be doubled** (`{{ }}`).

**Integrity check:** `tables.<name>.validation.integrity_check` with `source`, `query`, `summary`, `testcase`.

**Schema validation:** one block with `source`, `target`, `sourcequery`, `targetquery` (both use `{table_name}`), `datatype_map` (source type → expected target type), and `tables.<table>.source_column_exclusion / target_column_exclusion`.

**Incremental runs** append ` WHERE created_date between <from_date> and <to_date>` to both queries (see §7 for the caveats).

Many bronze YAMLs are machine-generated ("GENERATED FILE — do not hand-edit"); regenerate rather than editing by hand.

---

## 5. Output

```
output/<layer>/validation_<run_id>/                       (reports/sanity: output/<layer>/<pack>/validation_<run_id>/)
├── validation_<run_id>.log                               full DEBUG log of the run
├── schema_validation_<run_id>/
│     schema_validation_result_summary.xlsx               summary + sheets for each failing check type
├── count_validation_<run_id>/
│     count_validation_summary.csv
│     <table>_result.xlsx           (one "<table>_summary" sheet)
├── data_validation_<run_id>/
│     <validation>_result_summary.xlsx    (sheet "summary", all tables)
│     <table>_result.xlsx                 summary sheet + mismatch sheets:
│                                           <column>, column_counts, Missing_in_Source, Missing_in_Target
└── integrity_check_<run_id>/
      integrity_check_summary.csv
      <table>_result.csv                (only when offending rows were found)
```

- `run_id` format: `YYYYMMDD_HHMMSS_ffffff`.
- Mismatch sheets are capped at **2,000 rows** each; the true counts are in the summary (`missing_in_source`, `missing_in_target`, `mismatch_count`) and `column_counts`.
- Summary columns include `status` (`PASS`/`FAIL`), `source_count`, `target_count`, timings and `error_message` (full traceback when a query/config error occurred).

---

## 6. Exit codes

`main.py` itself only sets `0` or `1` (`sys.exit(1 if system_error else 0)`).

| Code | Set by | Meaning |
|---|---|---|
| `0` | `main.py` | Run completed with no system error. **Data mismatches (FAIL) alone do not change the exit code** – they are only visible in the summary files and log (`Total failures: N`). |
| `1` | `main.py` | A system error occurred: a validator crashed or any table raised an exception (DB/network error, bad YAML, missing creds, bad query, …). Also returned by Python when an exception is uncaught at startup (e.g. `--layer_type gold`). |
---

## 7. Expected errors

### Argument errors (exit code 2, before any DB connection)

| Message | Cause |
|---|---|
| `Both from and to dates must be provided!` | `--run_type incremental` without both dates |
| `--from-date and --to-date can only be used with --incremental` | Dates given on a historical run |
| `Invalid date: ... Expected format YYYY-MM-DD` | Bad date format |
| `Only the parameter --data_validation = 'yes' is accepted.` | `reports` layer with count or schema = `yes` |
| `--report_pack is required when --layer_type is 'sanity' or 'reports'.` | Missing pack |
| `--report_pack is only allowed when --layer_type is 'sanity' or 'reports'.` | Pack given for another layer |
| `--schema_validation 'yes' is only allowed when ... 'bronze_mssql' or 'bronze_postgres'.` | Intended: schema = yes on another layer. **Currently fires for any value on non-bronze layers (§8).** |
| `argument --x: invalid choice` / `the following arguments are required` | Typos or missing mandatory flags |

