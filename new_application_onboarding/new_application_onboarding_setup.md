# New application onboarding setup

This page is for onboarding a new application. Read it top to bottom once. Then fill in the **Your answers** block at the end and send it back.

Everything about the source and the target (database names, schemas, table names, types) comes from the **master workbook** in `yaml_generation/master_files/`. Nothing else is read for them.

**Prefer clicking to reading?** Open `docs/YAML_GENERATION_FLOW_EXPLORER.html` in a browser and choose the **Add a new source, step by step** tab. It asks for your names once, then gives you every step with paste-ready text and a **Copy** button.

## What is in this folder

| Item | What it is |
|---|---|
| `new_application_onboarding_setup.md` | This guide: the questions, with examples. |
| `templates/TEMPLATE_bronze_master.xlsx` | The workbook to copy. It has the right sheet names and headers, one example row, and a `READ_ME` sheet that explains each column. |

## Step 0: Choose the route (decide this first)

The framework reads **one workbook per layer**. A layer is the `--layer_type` you run, such as `bronze_postgres`. So the first question is where the new application goes.

**Question 0: Which route?**

- **Route A: same engine, same layer.** The new application is on PostgreSQL or SQL Server, which the framework already supports. Its rows go into the existing workbook for that layer (for PostgreSQL, `edge_bronze_master.xlsx`). No code change for the workbook. Its tables run with the existing `--layer_type`.
- **Route B: new layer.** The new application gets its own layer (for example `bronze_newbook`) and its own workbook. This needs a developer, because the code has to know the new layer (see Step 7).

Prompt to answer:

```
Route (A or B):
Why:
Engine it runs on (PostgreSQL / SQL Server / other):
```

Example answer: `Route B. Why: it is a new application with its own tables. Engine: PostgreSQL.`

> If you are not sure, answer `Route ?` and say what you know. We will decide together.

## Step 1: Application details

These names appear in the workbook and in the logs.

| Prompt | What to enter | Example | Rules |
|---|---|---|---|
| Application name | The name people use for it | `NewApp` | Letters and numbers. Used as `<application>` in the file name. |
| Layer name | The `--layer_type` you run (Route B only) | `bronze_newbook` | Ask a developer to confirm it. |
| Engine | The database engine of the source | `PostgreSQL` | One of the engines the framework supports, or tell us it is new (Step 6). |

Prompt to answer:

```
Application name:
Layer name (Route B only):
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

**Sheet 1: the tables sheet.** Any sheet name works, as long as it has these headers. Names are matched **ignoring case**, so `Target Table Name` and `target table name` are the same.

| Column header | What to put in it | Example row |
|---|---|---|
| `Target Table Name` | The table name | `units` |
| `Historical Filter to apply.` | An optional SQL condition, or empty for the whole table | (empty) |
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

**Sheet 3: `READ_ME`.** It is optional and is skipped by the framework. Keep it, or delete it.

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

## Step 6: A new database engine (only if the engine is not supported yet)

A new engine needs one block of rules in `db_rules.py`. Here is what each field means, with the PostgreSQL value as an example.

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

A new engine also needs a connector in `db/` and a layer in `main.py`. A developer does that.

## Step 7: What a developer must add in code (not done by this folder)

This folder does not change the code. For a **Route B** (new layer), a developer adds these:

1. `yaml_generation/filter_files.py`, `WORKBOOKS`: the layer name and its workbook file. Currently it has only `postgres` and `mssql`.
2. `yaml_generation/filter_files.py`, `SCHEMA_SHEETS`: the layer name and its mapping sheet name.
3. `yaml_generation/db_rules.py`, `RULES`: one block with the layer's name as key. The framework reads `RULES[<layer>]` for the label. For a layer on an existing engine, copy that engine's block.
4. `main.py`: confirm the new `--layer_type` is accepted and that the right connector is used.

Ask the developer to confirm steps 3 and 4 before the run.

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
ROUTE (A or B) and why:
ENGINE:
LAYER NAME (route B only):

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

NEW ENGINE? (yes/no). If yes, fill the Step 6 prompts.
ANYTHING ELSE:
```
