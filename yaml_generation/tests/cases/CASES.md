# Case results: main.py for PostgreSQL and SQL Server

Each case asks one question. The answer is the result of running it. Run them with:

    python -m pytest yaml_generation/tests/cases -q

Databases are fakes, and the validators are stubs: a case shows what main.py writes and what it hands to the validators. No Snowflake or database access is needed, so these cases do not prove the checks pass on real data.

Exclusions come from two places. `settings/exclusions.yaml` lists columns per data source, and is bidirectional: a column under a source is left out of that source and of Snowflake. The rules workbook holds table-specific exclusions.

| Case | Database | Question | Answer | Test |
|---|---|---|---|---|
| PG-01 | PostgreSQL | With --filter_files yes on a historical run, is units' filter written into its YAML? | Works | `test_pg_01_filter_yes_historical_writes_the_filter_into_the_yaml` |
| PG-02 | PostgreSQL | With --filter_files no, is the filter ignored, so units is compared in full? | Works | `test_pg_02_filter_no_writes_the_whole_table` |
| PG-03 | PostgreSQL | With --filter_files yes, does a filter that the reader refuses ('or') load its table in full, with a warning, and the run goes on? | Works | `test_pg_03_a_refused_filter_loads_its_table_in_full_with_a_warning` |
| PG-04 | PostgreSQL | On an incremental run with --filter_files yes, are the window and units' incremental filter both in the YAML? | Works | `test_pg_04_incremental_run_has_the_window_and_the_incremental_filter` |
| PG-05 | PostgreSQL | With --yaml_generation yes, are both named tables written, and validated, with exit code 0? | Works | `test_pg_05_named_tables_are_generated_then_validated` |
| PG-06 | PostgreSQL | With --yaml_generation no and no YAML on disk for the named table, does main.py create it and then validate it? | Works | `test_pg_06_missing_yaml_is_created_before_validation_when_generation_is_off` |
| PG-07 | PostgreSQL | With --yaml_generation no and a YAML already on disk, is the YAML left alone even though the source has changed? | Works | `test_pg_07_existing_yaml_is_used_as_it_is_when_generation_is_off` |
| PG-08 | PostgreSQL | With --yaml_generation yes, is a YAML whose source has changed rewritten (UPDATED)? | Works | `test_pg_08_generation_yes_refreshes_a_changed_table` |
| PG-09 | PostgreSQL | With --yaml_generation yes and one table that is not in the source, does that table fail, do the others still validate, and does the run exit 1? | Works | `test_pg_09_table_not_in_the_source_fails_and_is_not_validated` |
| PG-10 | PostgreSQL | With --yaml_generation no and a table name that is in neither the YAML folder nor the source, does the run stop with exit 1 and validate nothing? | Works | `test_pg_10_missing_table_with_generation_off_is_not_created_and_exit_is_1` |
| PG-11 | PostgreSQL | With --yaml_generation yes, does a table with no primary key fail its data (exit 1), with its count still written, while the other named table is still validated? | Works | `test_pg_11_a_table_without_a_primary_key_fails_and_the_others_run` |
| PG-12 | PostgreSQL | With --tables all, are the tables taken from the postgres filter workbook, and not from every table in the source? | Works | `test_pg_12_tables_all_takes_the_names_from_the_postgres_workbook` |
| PG-13 | PostgreSQL | With --tables all and no workbook for postgres, does the run stop with exit 1 and write nothing? | Works | `test_pg_13_tables_all_without_workbook_names_is_refused` |
| PG-14 | PostgreSQL | With both validators no and --yaml_generation yes, is the YAML written and no validator run (exit 0)? | Works | `test_pg_14_generate_only_writes_yaml_and_runs_no_validator` |
| PG-15 | PostgreSQL | Does a rules row (postgres, public, units, exclude legacy_code) leave that column out of units' YAML? | Works | `test_pg_15_a_table_rule_excludes_its_column_for_that_table` |
| PG-16 | PostgreSQL | Does a table rule for units in mssql leave a postgres run's units alone (the rule is for its own database)? | Works | `test_pg_16_a_sql_server_rule_does_not_apply_to_postgres` |
| PG-17 | PostgreSQL | Does a rules row with no source_type stop the run with exit 1, and write nothing? | Works | `test_pg_17_a_rule_row_without_source_type_stops_the_run` |
| PG-18 | PostgreSQL | Is --from_date (removed) refused with exit 2, before any folder or YAML is created? | Works | `test_pg_18_a_misspelt_argument_is_refused_before_anything_is_written` |
| PG-19 | PostgreSQL | With legacy_code excluded under postgres, is it left out of the postgres side AND the Snowflake side? | Works | `test_pg_19_a_postgres_exclusion_leaves_the_column_out_of_both_sides` |
| PG-20 | PostgreSQL | With legacy_code excluded under snowflake, is it left out of a postgres run on both sides? | Works | `test_pg_20_a_snowflake_exclusion_applies_to_a_postgres_run_on_both_sides` |
| MS-01 | SQL Server | With --filter_files yes, is a SiteLink filter without a bind parameter (Units: UnitID > 100) written into the YAML? | Works | `test_ms_01_filter_yes_applies_a_plain_sitelink_filter` |
| MS-02 | SQL Server | With --filter_files yes, does Addresses (filter uses :scorpcode, no value) load in full with a warning, and still generate? | Works | `test_ms_02_a_bind_parameter_filter_loads_its_table_in_full` |
| MS-03 | SQL Server | With --filter_files no, is Units' SiteLink filter ignored? | Works | `test_ms_03_filter_no_writes_the_whole_table` |
| MS-04 | SQL Server | Without the mssql exclusion of uts, does Units (uTS rowversion) fail with exit 1, and write no YAML? | Works | `test_ms_04_rowversion_without_the_sql_server_exclusion_fails_the_table` |
| MS-05 | SQL Server | With the shipped exclusions file (mssql: uts), is Units written with uTS left out, and exit 0? | Works | `test_ms_05_the_shipped_mssql_exclusion_leaves_the_rowversion_out` |
| MS-06 | SQL Server | Does an exclusion of uts under postgres leave SQL Server's uTS in place, so Units still fails? | Works | `test_ms_06_a_postgres_exclusion_of_uts_does_not_reach_sql_server` |
| MS-07 | SQL Server | With --yaml_generation no and no YAML for Units, is it created and then validated? | Works | `test_ms_07_missing_yaml_is_created_when_generation_is_off` |
| MS-08 | SQL Server | On an incremental run, is the SQL Server window on dUpdated (not updated_at)? | Works | `test_ms_08_incremental_window_uses_dupdated` |
| MS-09 | SQL Server | On an incremental run, is Addresses (no dUpdated on either side) written with the window by name on both sides, with a WARNING for each side, and exit 0? | Works | `test_ms_09_an_incremental_table_without_dupdated_is_written_with_the_window_by_name` |
| MS-10 | SQL Server | Does Lookup (no primary key) fail its data with exit 1, with its count still written? | Works | `test_ms_10_a_table_without_a_primary_key_fails_its_data_and_still_counts` |
| MS-11 | SQL Server | With --tables all, are the tables Units, Addresses and Lookup (the SiteLink workbook), and not Charges (in the source only)? | Works | `test_ms_11_tables_all_takes_the_names_from_the_sitelink_workbook` |
| MS-12 | SQL Server | With a target schema that does not exist, does the run stop with exit 1 and write no YAML? | Works | `test_ms_12_a_wrong_target_schema_writes_nothing` |
| MS-13 | SQL Server | With dcwidth excluded under snowflake, is it left out of a SQL Server run on both sides? | Works | `test_ms_13_a_snowflake_exclusion_applies_to_a_sql_server_run_on_both_sides` |

## Not covered by these cases

- Real databases and Snowflake: no access was available, so nothing here reads a live system.
- The validators themselves: the stubs record what they are given, and do not compare data.
- The live PostgreSQL catalogue test (`yaml_generation/tests/test_live_postgres.py`) stays opt-in.
