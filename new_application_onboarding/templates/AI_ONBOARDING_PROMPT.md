# Master prompt: onboard an application with an AI assistant

Fill in the blanks marked `<...>`, delete the routes that don't apply, and paste everything inside the box into an AI coding assistant opened in this repository.

The **Add a new source** tab of `YAML_GENERATION_FLOW_EXPLORER.html` builds this prompt for you, already filled in, once you've answered its questions.

**Pick the route first.** Two questions decide it:

1. Can an existing layer's connection reach the tables (Edge's PostgreSQL database `fms`, or SiteLink's SQL Server)? If so, it's **route A** or **route B**.
2. If not, the application needs a layer of its own. Does `db/factory.py` already have a connector for its database: PostgreSQL, SQL Server, MySQL, Snowflake, Athena or Redshift? If yes, it's **route C** (like Newbook, which reuses `Snowflake`). If no, it's **route D** (like MySQL, which needed `db/mysql.py`).

```text
You are onboarding a source into the Storable data validation framework (this repository). Read the files before you change them.

ROUTE (keep one, delete the others):
  A. More tables for <Edge or SiteLink>, which is already set up (layer <bronze_postgres or bronze_mssql>).
  B. A new application in the existing layer <bronze_postgres or bronze_mssql>: the existing connection reaches its tables. No code change.
  C. A new layer bronze_<key> that REUSES the existing <Postgres | Mssqlserver | Mysql | Snowflake | Athena | Redshift> connector
     (db/<file>.py). Do not add a new file in db/, an import or a package.
  D. A new layer bronze_<key> on a database engine with NO connector yet: write the connector (db/<key>.py), import it in
     db/factory.py and add its driver to requirements.txt.

FACTS
  Application ........: <name, e.g. Marketplace>
  Engine / layer name : <e.g. MySQL> / --layer_type <bronze_<key>>
  Master workbook ....: yaml_generation/master_files/<key>_bronze_master.xlsx   (route A/B: the existing layer's workbook)
  Source database ....: <the real database name, never the engine name>
  Source schema ......: <e.g. public>
  Snowflake database .: <{env}_<APPLICATION>_BRONZE>   (keep {env}; it becomes DEV, STG or PRD at run time)
  Snowflake schema ...: <e.g. MARKETPLACE_PUBLIC>
  Tables .............: <one per line; the first should be small>
  Source types in use : <e.g. int, varchar, datetime> -> Snowflake type for each, or "not sure"
  Incremental runs ...: <yes, as well as historical | no, historical only>
Any value still written <...> is unknown: ask me for it before you use it.

FILES TO CHANGE
  Every route:
    - yaml_generation/master_files/<workbook>: one row per table on Sheet1, and a row per source type on '<Label> schema mapping'.
      A new workbook starts as a copy of new_application_onboarding/templates/TEMPLATE_bronze_master.xlsx.
    - yaml_generation/settings/dev_rules.xlsx, sheet table_rules: only for a table that needs key_columns, exclude_columns or a filter.
  Routes C and D also:
    - yaml_generation/filter_files.py: add "<key>" to WORKBOOKS and to SCHEMA_SHEETS.
    - yaml_generation/db_rules.py: add a "<key>" block to RULES and "<key>" to SOURCE_TYPES.
        C: a copy of the reused database's block (newbook for Snowflake), with only the key and the label changed.
        D: a new block written for the engine (start from the mysql block); add a test in yaml_generation/tests/test_db_rules.py.
    - yaml_generation/settings.py: add "<key>" to INCREMENTAL_COLUMNS.   yaml_generation/settings/exclusions.yaml: a "<key>:" block.
    - db/factory.py: an elif db_type == "<key>" branch in get_database(), before the final else.
        C: it builds the existing class from file['<key>'], like the newbook branch builds Snowflake. No new import.
        D: it builds the new class; also add "from db.<key> import <Class>" with the other imports at the top.
    - creds/<env>.yaml (every environment, kept local) and creds/example.yaml (field names only): a "<key>:" block.
    - utils/utility.py: add "bronze_<key>" at the end of BRONZE_LAYERS.
  Route D also:
    - db/<key>.py (new): the connector class, based on db/base.py's Database, like db/mysql.py: connect(),
      execute_query(query) returning a pandas DataFrame, close().
    - requirements.txt: the driver package; then pip install -r requirements.txt.
  Incremental runs (routes C and D) also:
    - utils/utility.py: a "bronze_<key>" branch in check_freshness() and in get_dates().
    - creds/api.yaml: the Fivetran connection id under the key bronze_<key>.
    - control_table.csv: one line per table: <table in lower case>,<last count run>,<last data run>,bronze_<key>.

RULES
  - The master workbook is the only source of truth for locations and filters. Keep its two sheets and their headers exactly:
    Sheet1: Target Table Name | Historical Filter to apply | Incremental filters to apply | source_application | source_database |
            source_schema | target_application | target_database | target_schema
    '<Label> schema mapping': source_data_type | target_data_type | precision_check | notes
    No extra sheets or columns. target_application is Snowflake. target_database must contain {env}.
  - When you add to a list or dict (WORKBOOKS, SCHEMA_SHEETS, SOURCE_TYPES, BRONZE_LAYERS, INCREMENTAL_COLUMNS, RULES), keep every existing entry.
  - Use the same key everywhere: '<key>' in RULES, SOURCE_TYPES, WORKBOOKS, SCHEMA_SHEETS, INCREMENTAL_COLUMNS, exclusions.yaml,
    get_database() and the creds block; the layer is 'bronze_<key>'.
  - Every edit to a shared file outside yaml_generation/ gets a comment starting '# CHANGE: YAML generation.' that says what and why.
  - Make only the changes this plan needs: no new flags, helpers or refactors. Do not change other layers.
  - Never put passwords in the repository: creds/<env>.yaml stays local; only creds/example.yaml lists the field names.
  - Do not run a huge table, or all tables, without a filter.

CHECKS WHEN YOU ARE DONE
  python -m pytest yaml_generation/tests -q
  python yaml_generation/filter_files.py
  python main.py --layer_type <layer> --tables <first table> --run_type historical --environment dev --schema_validation yes --count_validation no --data_validation no
Report the files you changed, what you could not do, and the output of each check.
```
