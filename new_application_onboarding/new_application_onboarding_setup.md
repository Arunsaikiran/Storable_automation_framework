# New application onboarding setup

This page is for onboarding a new application. Read it top to bottom once. Then fill in the **Your answers** block at the end and send it back.

Everything about the source and the target (database names, schemas, table names, types) comes from the **master workbook** in `yaml_generation/master_files/`. Nothing else is read for them.

**Prefer clicking to reading?** Open `new_application_onboarding/YAML_GENERATION_FLOW_EXPLORER.html` in a browser and choose the **Add a new source, step by step** tab. It asks for your names once, then gives you every step with paste-ready text and a **Copy** button.

**Want an AI assistant to make the changes?** Fill in `templates/AI_ONBOARDING_PROMPT.md` and paste it into the assistant. The explorer also builds the same prompt for you, already filled in.

## What is in this folder

| Item | What it is |
|---|---|
| `new_application_onboarding_setup.md` | This guide: the questions, with examples. |
| `YAML_GENERATION_FLOW_EXPLORER.html` | The same steps as a click-through guide, with copy buttons and the AI prompt. |
| `templates/TEMPLATE_bronze_master.xlsx` | The workbook to copy. It has the two standard sheets (`Sheet1` and `Postgres schema mapping`) with the right headers and one example row each, and nothing else. |
| `templates/AI_ONBOARDING_PROMPT.md` | The master prompt for an AI assistant: the facts to fill in, the files for each route, the house rules and the checks. |

## Step 0: Choose the route (decide this first)

The framework reads **one workbook per layer**. A layer is the `--layer_type` you run, such as `bronze_postgres`. Two questions decide the route.

**Question 1: Can an existing layer's connection reach the tables?** Today that means Edge's PostgreSQL database `fms`, or SiteLink's SQL Server.

- **Route A: more tables** for Edge or SiteLink. You add one row per table to that layer's workbook. No code change.
- **Route B: a new application in an existing layer.** The application's tables are reachable through Edge's or SiteLink's connection. Its rows go into that layer's workbook (for PostgreSQL, `edge_bronze_master.xlsx`), and its tables run with the existing `--layer_type`. No code change.

**Question 2 (only if the answer to question 1 is no): does the framework already have a connector for its database?** A connector is the class in `db/` that opens the connection. There is one for **PostgreSQL, SQL Server, MySQL, Snowflake, Athena and Redshift**: they are the branches of `get_database()` in `db/factory.py`.

- **Route C: a layer of its own that reuses a connector.** Example: Newbook's source is Snowflake, so `bronze_newbook` builds the existing `Snowflake` class from its own creds block. A developer adds the layer's entries (Step 7). There's **no new file in `db/`, no new import and no new package**.
- **Route D: a layer of its own on a completely new engine.** Example: before `bronze_mysql_crm` there was no MySQL connector. A developer does everything in route C, and also writes the connector `db/<key>.py`, imports it at the top of `db/factory.py` and adds its driver to `requirements.txt` (Step 7).

Prompt to answer:

```
Route (A, B, C or D):
Why:
Engine it runs on (PostgreSQL / SQL Server / MySQL / Snowflake / other):
Connector it reuses (route C only):
```

Example answers: `Route C. Why: its own Snowflake database, and we have the Snowflake connector. Engine: Snowflake. Connector: Snowflake.` or `Route D. Why: Oracle, and there is no Oracle connector. Engine: Oracle.`

> If you are not sure, answer `Route ?` and say what you know. We will decide together.

## Step 1: Application details

These names appear in the workbook and in the logs.

| Prompt | What to enter | Example | Rules |
|---|---|---|---|
| Application name | The name people use for it | `NewApp` | Letters and numbers. Used as `<application>` in the file name. |
| Layer name | The `--layer_type` you run (routes C and D only) | `bronze_newbook` | `bronze_` plus a new lower-case key. The same key names the workbook, the rules block and the creds block. It can't be one already in use (`postgres`, `mssql`, `mysql`, `athena`, `redshift`, `snowflake`, `newbook`). |
| Engine | The database engine of the source | `PostgreSQL` | One of the engines the framework supports, or tell us it is new (Step 6). |

Prompt to answer:

```
Application name:
Layer name (routes C and D only):
Engine:
```

## Step 2: Source database

The source is the database the application writes to. The workbook needs the real database name, not the engine.

| Prompt | What to enter | Example | Rules |
|---|---|---|---|
| Source database name | The real name on the source server | `fms` | Not `postgres` or `sqlserver`. The framework stops with an error if you write the engine name. |
| Source schema | The schema the tables live in | `public` | Must match the source server. |

Prompt to answer:

```
Source database name:
Source schema:
```

## Step 3: Target (Snowflake)

The target is always Snowflake.

| Prompt | What to enter | Example | Rules |
|---|---|---|---|
| Target application | Always `Snowflake` | `Snowflake` | Any other value is refused. |
| Target database | The Snowflake database, with `{env}` in it | `{env}_NEWAPP_BRONZE` | `{env}` is required. It becomes `dev`, `stg` or `prod` at run time. Without it the framework refuses the row. |
| Target schema | The Snowflake schema | `NEWAPP_SCHEMA` | Case does not matter. |

Prompt to answer:

```
Target database (must contain {env}):
Target schema:
```

## Step 4: The Excel workbook (the master file)

**File name:** `<application>_bronze_master.xlsx`, for example `newapp_bronze_master.xlsx`.

Copy `templates/TEMPLATE_bronze_master.xlsx` and rename it. Then:

Every master workbook has the **same two sheets and nothing else**: `Sheet1` and `<Engine> schema mapping`. Don't add columns or sheets (counts, notes, test results). Anyone who copies the file later copies them too. Keep that kind of work in a separate file.

**Sheet 1: `Sheet1`, the tables sheet.** It has exactly these nine headers, in this order. Names are matched **ignoring case**, so `Target Table Name` and `target table name` are the same.

| Column header | What to put in it | Example row |
|---|---|---|
| `Target Table Name` | The table name | `units` |
| `Historical Filter to apply` | An optional SQL condition, or empty for the whole table | (empty) |
| `Incremental filters to apply` | An optional SQL condition, or empty | (empty) |
| `source_application` | The application name | `NewApp` |
| `source_database` | The real source database name (Step 2) | `fms` |
| `source_schema` | The source schema (Step 2) | `public` |
| `target_application` | Always `Snowflake` | `Snowflake` |
| `target_database` | The Snowflake database with `{env}` (Step 3) | `{env}_NEWAPP_BRONZE` |
| `target_schema` | The Snowflake schema (Step 3) | `NEWAPP_SCHEMA` |

**First row (example):**

```
Target Table Name | Historical Filter | Incremental filters | source_application | source_database | source_schema | target_application | target_database      | target_schema
units             | (empty)           | (empty)             | NewApp            | fms             | public        | Snowflake         | {env}_NEWAPP_BRONZE  | NEWAPP_SCHEMA
```

**Rows:** one row per table. A table listed twice keeps its first row, and the second is ignored.

**Sheet 2: the type mapping sheet.** Its name is `<Engine> schema mapping`, for example `Postgres schema mapping` or `MSSQL schema mapping`. Columns: `source_data_type`, `target_data_type`, `precision_check`, `notes`.

| Column | What to put in it | Example |
|---|---|---|
| `source_data_type` | The type as the source database names it. **Case does not matter.** `NUMBER` and `number` are the same. | `integer` |
| `target_data_type` | The Snowflake type: `number`, `text`, `boolean`, `float`, `date`, `timestamp_ntz`, `timestamp_tz` or `variant` | `number` |
| `precision_check` | Empty, or one of `numeric`, `datetime`, `character` | `numeric` |
| `notes` | Free text, for people | `from the source catalogue` |

Each source type the tables use needs one row. If a type has no row, that table's schema check **fails** and the framework writes nothing for it. The message names the missing types.

Prompt to answer:

```
Workbook file name:
Tables (one name per line):
Source database name:
Source schema:
Target database (with {env}):
Target schema:
Source types the tables use (copy them from the source database, any case):
```

## Step 5: Which types does the new application use?

The mapping needs one row per source type. To find them, ask the source database for the types of the new tables, or ask the person who owns the source.

Prompt to answer:

```
Source types in use (for example: integer, character varying, timestamp without time zone, boolean):
Target type for each (Snowflake type, if you know it; otherwise write "not sure"):
```

If you write "not sure", the developer checks the Snowflake column type with `DESCRIBE TABLE`.

## Step 6: A new database engine (route D only)

A new engine needs one block of rules in `db_rules.py`. For route C, skip this step: the layer copies the block of the database it reuses (`newbook` for Snowflake), and changes only the key and the label. Here is what each field means, with the PostgreSQL value as an example.

| Field | What it means | PostgreSQL example |
|---|---|---|
| `label` | The engine's name in messages | `PostgreSQL` |
| `has_primary_keys` | Does the catalogue have primary keys we can read? | `True` |
| `quote` | How names are quoted, open and close | `"` and `"` |
| `text_type` | The type a value is converted to, so both sides become text | `TEXT` |
| `true`, `false` | How TRUE and FALSE are written | `true`, `false` |
| `trim` | SQL that trims spaces; `{c}` is the column | `TRIM({c})` |
| `timestamp`, `date` | SQL that writes a timestamp or date as text | `TO_CHAR({c}, 'YYYY-MM-DD HH24:MI:SS.US')` |
| `boolean_types`, `date_types`, `timestamp_types`, `text_types`, `large_text_types`, `integer_types` | The type names this engine reports for each kind | `{"boolean"}`, `{"integer", "bigint"}` |
| `refused_types` | Types that cannot be compared as text here | empty |
| `schema_query` | The catalogue query for the schema check | (copy from the PostgreSQL block) |

Prompt to answer:

```
New engine name (lower case, letters, digits and _ only, e.g. mysql):
Label (how people write it):
Does it have primary keys? (yes / no):
Quote characters (open and close):
Text type to convert to:
How TRUE and FALSE are written:
Type names for boolean, date, timestamp, text, integer (copy from the database):
Anything that cannot be compared as text:
```

A new engine also needs a connector in `db/`. A developer writes it (Step 7, route D).

## Step 7: What a developer must add in code (routes C and D)

This folder does not change the code. Routes A and B need no code change. In each file below, **add** the new entry and keep every entry that's already there. Use the same key (`<key>`, the layer without `bronze_`) everywhere. Mark every edit outside `yaml_generation/` with a `# CHANGE: YAML generation.` comment.

**Routes C and D:**

1. `yaml_generation/filter_files.py`: add `"<key>"` to `WORKBOOKS` (its workbook file) and to `SCHEMA_SHEETS` (its mapping sheet name).
2. `yaml_generation/db_rules.py`: add a `"<key>"` block to `RULES`, and `"<key>"` to `SOURCE_TYPES`. Route C copies the reused database's block. Route D writes a new one (Step 6).
3. `yaml_generation/settings.py`: add `"<key>"` to `INCREMENTAL_COLUMNS`. `yaml_generation/settings/exclusions.yaml`: add a `<key>:` block.
4. `db/factory.py`: add an `elif db_type == "<key>":` branch in `get_database()`, before the final `else`.
   - **Route C:** the branch builds the **existing** class from `file['<key>']`, the way the `newbook` branch builds `Snowflake`. Don't add an import or a package: the class is already imported.
   - **Route D:** the branch builds the new class from step 6 below.
5. `creds/<env>.yaml` (every environment; kept local, never committed) and `creds/example.yaml` (field names only): add a `<key>:` block. For route C, copy the reused connector's block and point it at the new database.
6. `utils/utility.py`: add `"bronze_<key>"` at the end of `BRONZE_LAYERS`. The `--layer_type` choices and the YAML generation all read this one tuple.

**Route D also needs:**

7. `db/<key>.py` (new file): the connector class. It's based on `Database` in `db/base.py`, with `connect()`, `execute_query(query)` returning a pandas DataFrame, and `close()`. Copy `db/mysql.py`, the newest connector.
8. `db/factory.py`, at the top: `from db.<key> import <Class>`, with the other imports. Without it the branch from item 4 fails with `NameError`.
9. `requirements.txt`: add the driver package (MySQL uses `pymysql`), then run `pip install -r requirements.txt`.

**Incremental runs (routes C and D):** add a `bronze_<key>` branch to `check_freshness()` and to `get_dates()` in `utils/utility.py`. Also add the Fivetran connection id under `bronze_<key>` in `creds/api.yaml`, and one line per table in `control_table.csv`.

Then run `python -m pytest yaml_generation/tests -q` and `python yaml_generation/filter_files.py`. Both must pass before the first run.

## Step 8: Run it

Once the workbook is in `yaml_generation/master_files/` and the layer is set up:

```
python main.py --layer_type <layer> --tables <table names or all> --schema_validation yes --count_validation no --data_validation no --environment dev
```

Use `--tables all` to run every table in the workbook, or name the tables with spaces between them.

What the result means:

- **NEW / UPDATED / UNCHANGED / PRESENT:** the YAML is ready.
- **FAILED:** that table's check failed, with the reason and "nothing was changed." Fix the reason in the workbook and run again.

## Known rules (so you do not hit them by surprise)

- Table names, column headers, sheet names and engine names ignore case.
- Type names (the mapping) ignore case. `NUMBER` matches `number`.
- `source_database` is the real database name. The framework stops if you write the engine name.
- `target_application` must be `Snowflake`.
- `target_database` must contain `{env}`.
- Data validation needs a primary key on the source table. Schema validation does not.
- A source type with no mapping row fails that table's schema check, and the message names the type.

## Your answers

Copy this block, fill it in, and send it back.

```
ROUTE (A, B, C or D) and why:
ENGINE:
CONNECTOR IT REUSES (route C only):
LAYER NAME (routes C and D only):

APPLICATION NAME:

SOURCE DATABASE NAME (real name, not the engine):
SOURCE SCHEMA:

TARGET APPLICATION: Snowflake
TARGET DATABASE (must contain {env}):
TARGET SCHEMA:

WORKBOOK FILE NAME:
TABLES (one per line):

SOURCE TYPES IN USE:
TARGET TYPE for each (or "not sure"):

NEW ENGINE, NO CONNECTOR YET? (yes = route D: fill the Step 6 prompts)
ANYTHING ELSE:
```
