# Storable SQL Comparison / Data Validation Framework

## 1. Architecture

There is no separate validator/plugin engine. `main.py` is a single script that acts as CLI, orchestrator, comparison engine, and report writer. It is supported by two small packages:

```
Storable_automation_framework/
├── main.py                    # CLI, dispatch, comparison logic, exit-code decision
├── db/
│   ├── base.py                 # abstract Database interface (connect / execute_query)
│   ├── factory.py               # get_database(db_type, ...) -> Postgres|Mssqlserver|Snowflake|Redshift|Athena
│   ├── postgres.py, mssqlserver.py, snowflake.py, redshift.py, athena.py
├── utils/utility.py             # run-id generation, config/output path resolution, summary CSV writer, logging
├── creds/{dev,uat,prod,local}.yaml   # per-environment DB credentials (gitignored)
├── config/
│   ├── bronze_mssql/{count_validation,data_validation}/*.yaml
│   ├── bronze_postgres/{count_validation,data_validation}/*.yaml
│   ├── silver/{count_validation,data_validation}/*.yaml
│   ├── gold/                    # placeholder layer, no yaml files exist yet
│   ├── sanity/integrity_check.yaml
│   └── reports/<report_pack>/data_validation/*.yaml   # emanagement, egrowth, eperformance, ...
└── output/<layer_type>[/<report_pack>]/validation_<run_id>/
        {count_validation_<id>, data_validation_<id>, integrity_check_<id>}/
        validation_<run_id>.log, *_summary.csv, <table>_result.xlsx|csv
```

**Two independent axes drive every run:**

- `--layer_type` selects *which config subtree* is read: `bronze_postgres`, `bronze_mssql`, `silver`, `gold`, `reports`, `sanity`.
- `--count_validation` / `--data_validation` select *which validation mechanism* runs inside that layer's config.

There are exactly **three validation mechanisms**, not per-metric "test types":

| Mechanism | Trigger | What it does |
|---|---|---|
| `count_validation` | `validation_name == "count_validation"` | Runs `sourcequery`/`targetquery`, compares `source_row_count` vs `target_row_count` (single-row result required). |
| `data_validation` | anything else under `validations:` | Runs both queries into DataFrames, indexes on `sourcecolumn`/`targetcolumn` (comma-separated for composite keys), sorts, and does a full `DataFrame.equals()` comparison. |
| `integrity_check` (sanity layer only) | `layer_type == sanity` | Runs a single `query`, expects 0 rows back; any row returned is a failure (orphan/null-key style checks). |

"Sum", "count", "avg", "distinct" report files (e.g. `Test_02_Sum_athena.yaml`) are **not** separate code paths — they are `data_validation` entries whose SQL happens to aggregate differently. The filename is just a naming convention for humans.

Config files under `config/*/data_validation/` for `bronze_postgres` are machine-generated from an external plan/generator pipeline (see file headers: "GENERATED FILE — do not hand-edit"). Treat them as build artifacts, not hand-authored config, unless a file's header says otherwise.

### Supported connectors (`db/factory.py`)

`source:` / `target:` in a YAML must be exactly one of: `postgres`, `mssql`, `athena`, `snowflake`, `redshift`. Any other string (e.g. `postgresql`, `sqlserver`) raises `ValueError: Unsupported database: <name>` at runtime — it will not be caught until that specific table is processed.

Credentials come only from `creds/<environment>.yaml` — there is no `.env` support. `--environment` accepts `dev, stg, qat, prod, local`, but only `dev.yaml`, `uat.yaml`, `prod.yaml`, `local.yaml` exist on disk today; `stg`/`qat` are accepted by argparse but have no backing creds file.

Snowflake target queries can contain a literal `{env}` placeholder (e.g. `FROM {env}_EDGE_BRONZE...`) which `main.py` fills in based on `--environment` (e.g. `DEV`, `QAT`, `PROD`). This only applies to Snowflake target queries — source queries and non-templated targets are unaffected.

---

## 2. Running each layer

Base command shape:

```bash
python main.py --layer_type <layer> [--report_pack <pack>] --tables <name(s)|all> --count_validation <yes|no> --data_validation <yes|no> --environment <dev|stg|qat|prod|local>
```

### bronze_postgres / bronze_mssql / silver
Standard layers, both validation flags are usable freely.

```bash
python main.py --layer_type bronze_postgres --tables all --count_validation yes --data_validation yes --environment dev
```

### gold
Argparse accepts it, but `config/gold/` currently has no YAML files. Any run against `gold` fails immediately with:
```
FileNotFoundError: [Errno 2] No such file or directory: '...\config\gold\count_validation\gold.yaml'
```
Do not use `gold` until config files are added for it.

### reports
Requires `--report_pack` (`emanagement`, `smanagement`, `egrowth`, `sgrowth`, `eperformance`, `sperformance`) and **only supports `--data_validation`**. There is no `count_validation` subfolder for report packs.

```bash
python main.py --layer_type reports --report_pack emanagement --tables all --count_validation no --data_validation yes --environment dev
```

**Expected error — count validation not accepted for reports:**
```bash
python main.py --layer_type reports --report_pack emanagement --tables all --count_validation yes --data_validation no --environment dev
```
```
main.py: error: --count_validation is not supported for layer_type=reports; only --data_validation is accepted.
```
This is an explicit `argparse.parser.error()` guard — it exits with code `2` before any config is read or DB connection made.

**Expected error — missing `--report_pack`:**
```
TypeError: 'NoneType' object is not subscriptable
```
Unhandled traceback; always pass `--report_pack` for `reports`.

### sanity
Runs the single `config/sanity/integrity_check.yaml`. Unlike `reports`, it does **not** reject `count_validation` — both flags map to the same integrity-check flow, so passing either (or both) as `yes` runs the same checks. Passing `no` to both simply means no tables get processed for that run.

```bash
python main.py --layer_type sanity --tables all --count_validation no --data_validation yes --environment dev
```

---

## 3. Other expected errors

| Scenario | Result |
|---|---|
| `--tables <name not in config>` | `ValueError: No tables found to process.` |
| YAML `source`/`target` not in `{postgres, mssql, athena, snowflake, redshift}` | `ValueError: Unsupported database: <name>` — caught by the generic exception handler, logged, table marked FAIL, run **continues**, exit code stays `0`. |
| `--environment` has no matching `creds/<env>.yaml` (e.g. `stg`, `qat` today) | `FileNotFoundError` — same generic handling as above, exit code stays `0`. |
| Postgres/MSSQL connection failure (`psycopg2.Error` / `pyodbc.Error`) | Explicitly caught, sets `system_error = True` → process exits with code `1`. |
| Snowflake/Athena/Redshift connection failure | Falls through to the generic exception handler — logged as FAIL, but does **not** set `system_error`, so exit code stays `0`. This asymmetry matters for CI gating: only Postgres/MSSQL outages currently fail the build. |

**Exit code summary:** `sys.exit(1 if system_error else 0)` — `1` only means a Postgres/MSSQL connectivity error occurred somewhere in the run; every other failure (bad config, unsupported connector, missing creds file, PASS/FAIL mismatches) exits `0` and must be checked via the summary CSV or log, not the exit code.

---

## 4. Output

Each run writes to `output/<layer_type>[/<report_pack>]/validation_<run_id>/`:
- `validation_<run_id>.log` — full run log.
- `<validation_type>_summary.csv` — one appended row per table/validation, PASS/FAIL plus counts (or comparison result).
- On mismatch: `<table>_result.xlsx` (data_validation, sheets: `Differences`, `Missing_in_Source`, `Missing_in_Target`) or `<table>_<check>_result_<run_id>.csv` (sanity/integrity_check).
