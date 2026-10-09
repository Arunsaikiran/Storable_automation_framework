import yaml
from db.factory import get_database, close_all_databases
from utils.utility import get_logger
from openpyxl import load_workbook
from datetime import datetime
import time
import pyodbc
import psycopg2
from utils.utility import create_summary,create_summary_schema
import traceback
import os
import pandas as pd
import numpy as np

logger = get_logger(__name__)

def validate_schema(ctx):
    run_id = ctx.run_id
    run_at = ctx.run_at
    layer = ctx.layer_type
    outputpaths = ctx.outputpaths
    configpaths = ctx.configpaths
    BASE_DIR = ctx.BASE_DIR
    environment = ctx.environment
    tables = ctx.tables
    # from_date = ctx.from_date
    # to_date = ctx.to_date

    failure_count = 0
    system_error = False

    validation = "schema_validation"
    output_path = outputpaths[validation]
    config_path_yaml = configpaths[validation]

    logger.info("Processing validation type: %s", validation)
    logger.debug("Output path: %s", output_path)
    logger.debug("Config path: %s", config_path_yaml)

    with open(config_path_yaml[0]) as f:
            config = yaml.safe_load(f)

    filepath = os.path.join(output_path, f"schema_validation_result_summary.xlsx")
    logger.info("Loaded configuration: %s", config_path_yaml[0])
    config_file = config['validations'][validation]
    table_config = config_file['tables']
    source = config_file['source']
    target = config_file['target']
    source_query = config_file['sourcequery']
    target_query = config_file['targetquery']
    datatype_map = config_file['datatype_map']
    print("="*100)
    print("Started...")

    if tables[0] == 'all':
        table_list = list(table_config.keys())
    else:
        table_list = tables
        invalid_tables = list(set(table_list) - set(list(table_config.keys())))
        if invalid_tables:
            raise ValueError(f"Invalid table name(s) in configuration: {', '.join(invalid_tables)}")

    validation_result = {}
    missing_in_source_df = pd.DataFrame()
    missing_in_target_df = pd.DataFrame()
    datatype_df = pd.DataFrame()
    datetime_df = pd.DataFrame()
    integer_df = pd.DataFrame()
    character_df = pd.DataFrame()

    for table_name in table_list:
        try:
            #Getting dataframe
            batch_start_time = datetime.now()
            logger.info("Executing schema validation check for table: %s", table_name)
            #Column Exclusion
            source_column_exclusion = config_file['tables'][table_name]['source_column_exclusion']
            target_column_exclusion = config_file['tables'][table_name]['target_column_exclusion']

            obj = get_database(source,BASE_DIR,environment)
            formatted_source_query = source_query.format(table_name = table_name)
            source_df = obj.execute_query(formatted_source_query)
            source_df = source_df[~source_df['column_name'].isin(source_column_exclusion)]
            source_df.columns = [f"{i}_source" for i in source_df.columns]
            logger.info("Source Query: %s", formatted_source_query)
            obj = get_database(target,BASE_DIR,environment)
            formatted_target_query = target_query.format(table_name = table_name.upper())
            target_df = obj.execute_query(formatted_target_query)
            target_df.columns = target_df.columns.str.lower()
            target_df = target_df[~target_df['column_name'].isin(target_column_exclusion)]
            target_df.columns = [f"{i}_target" for i in target_df.columns]

            for col in target_df.select_dtypes(include=['object','string']).columns:
                target_df[col] = target_df[col].str.strip().str.lower()
            source_df['table_name_source'] = source_df['table_name_source'].str.lower()
            source_df['column_name_source'] = source_df['column_name_source'].str.lower()
            # target_df['table_name_target'] = target_df['table_name_target'].str.lower()
            # target_df['column_name_target'] = target_df['column_name_target'].str.lower()

            #check

            logger.info("Target Query: %s", formatted_target_query)

            if source_df.empty or target_df.empty:
                if source_df.empty and target_df.empty:
                    error_message = f"Table '{table_name}' not found in source ({source}) or target ({target})"
                elif source_df.empty:
                    error_message = f"Table '{table_name}' not found in source ({source})"
                else:
                    error_message = f"Table '{table_name}' not found in target ({target})"
                logger.error(error_message)
                batch_end_time = datetime.now()
                diff_batch = batch_end_time - batch_start_time
                batch_start_time_str = batch_start_time.strftime("%H:%M:%S")
                batch_end_time_str = batch_end_time.strftime("%H:%M:%S")
                total_batch_time_taken = time.strftime("%H:%M:%S", time.gmtime(diff_batch.total_seconds()))
                create_summary_schema(
                    run_at, run_id, validation, table_name, source, None, target, "FAIL",
                    output_path, error_message=error_message,
                    batch_start_time=batch_start_time_str, batch_end_time=batch_end_time_str,
                    diff_batch=total_batch_time_taken, layer_type=layer[0]
                )
                continue

            join_df = source_df.merge(
                target_df,
                how='outer',
                left_on=['table_name_source','column_name_source'],
                right_on=['table_name_target','column_name_target'])
            join_df = join_df[~join_df['column_name_target'].isin(['_fivetran_active', '_fivetran_end', '_fivetran_start','_fivetran_synced'])]

            data_type_failure_counts = 0
            datetime_failure_counts = 0
            integer_failure_counts = 0
            character_failure_counts = 0
            source_column_name_failure_counts = 0
            target_column_name_failure_counts = 0
            #column name check
            table_result = validation_result.setdefault(table_name, {})

            if join_df['column_name_source'].isna().any():
                new_missing_in_source_df = join_df[join_df['column_name_source'].isna()][['table_name_target','column_name_target']]
                new_missing_in_source_df = new_missing_in_source_df.reset_index()
                table_result["missing_in_source"] = {"status":"FAIL","data":new_missing_in_source_df}
                source_column_name_failure_counts += join_df['column_name_source'].isna().sum()
                missing_in_source_df = pd.concat([missing_in_source_df, new_missing_in_source_df], ignore_index=True)

            else:
                table_result["missing_in_source"] = {"status":"PASS"}


            if join_df['column_name_target'].isna().any():
                new_missing_in_target_df = join_df[join_df['column_name_target'].isna()][['table_name_source','column_name_source']]
                new_missing_in_target_df = new_missing_in_target_df.reset_index()
                table_result["missing_in_target"] = {"status":"FAIL","data":new_missing_in_target_df}
                target_column_name_failure_counts += join_df['column_name_target'].isna().sum()
                missing_in_target_df = pd.concat([missing_in_target_df, new_missing_in_target_df], ignore_index=True)

            else:
                table_result["missing_in_target"] = {"status":"PASS"}

            join_df = (
                join_df[(~join_df['column_name_source'].isna()) &
                        (~join_df['column_name_target'].isna())
                        ])


            #data_type check
            datatype_rows = []
            for _,row in join_df[['table_name_source','column_name_source','column_name_target','data_type_source','data_type_target']].iterrows():
                if datatype_map[row['data_type_source']] == row['data_type_target']:
                    pass
                else:
                    data_type_failure_counts += 1
                    datatype_rows.append(row[['table_name_source','column_name_source','data_type_source','data_type_target']])
            if data_type_failure_counts == 0:
                table_result["datatype_validation"] = {"status":"PASS"}
            else:
                new_datatype_df = pd.DataFrame(datatype_rows).reset_index()
                table_result["datatype_validation"] = {"status":"FAIL","data":new_datatype_df,"failure_counts":data_type_failure_counts}
                datatype_df = pd.concat([datatype_df, new_datatype_df], ignore_index=True)

            table_warning = "No"

            #Datetime precision
            datetime_rows = []
            for _,row in join_df[join_df['data_type_source'].isin(['timestamp without time zone'])].iterrows():
                if row['datetime_precision_source'] <= row['datetime_precision_target']:
                    if row['datetime_precision_source'] < row['datetime_precision_target']:
                        table_warning = "Yes"
                        r = row[['table_name_source','column_name_source','data_type_source','data_type_target','datetime_precision_source','datetime_precision_target']].copy()
                        r['warning'] = "Yes"
                        datetime_rows.append(r)
                else:
                    datetime_failure_counts += 1
                    r = row[['table_name_source','column_name_source','data_type_source','data_type_target','datetime_precision_source','datetime_precision_target']].copy()
                    r['warning'] = "No"
                    datetime_rows.append(r)
            new_datetime_df = pd.DataFrame(datetime_rows).reset_index() if datetime_rows else pd.DataFrame()
            if datetime_failure_counts == 0:
                table_result["datetime_precision_validation"] = {"status":"PASS"}
            else:
                table_result["datetime_precision_validation"] = {"status":"FAIL","data":new_datetime_df,"failure_counts":datetime_failure_counts}
            if not new_datetime_df.empty:
                datetime_df = pd.concat([datetime_df, new_datetime_df], ignore_index=True)

            #integer
            integer_rows = []
            for _,row in join_df[join_df['data_type_source'].isin(['integer'])].iterrows():
                if row['numeric_precision_source'] <= row['numeric_precision_target'] or row['numeric_scale_source'] <= row['numeric_scale_target']:
                    if row['numeric_precision_source'] < row['numeric_precision_target'] or row['numeric_scale_source'] < row['numeric_scale_target']:
                        table_warning = "Yes"
                        r = row[['table_name_source','column_name_source','data_type_source','data_type_target','numeric_precision_source','numeric_precision_target','numeric_scale_source','numeric_scale_target']].copy()
                        r['warning'] = "Yes"
                        integer_rows.append(r)
                else:
                    integer_failure_counts += 1
                    r = row[['table_name_source','column_name_source','data_type_source','data_type_target','numeric_precision_source','numeric_precision_target','numeric_scale_source','numeric_scale_target']].copy()
                    r['warning'] = "No"
                    integer_rows.append(r)
            new_integer_df = pd.DataFrame(integer_rows).reset_index() if integer_rows else pd.DataFrame()
            if integer_failure_counts == 0:
                table_result["integer_precision_validation"] = {"status":"PASS"}
            else:
                table_result["integer_precision_validation"] = {"status":"FAIL","data":new_integer_df,"failure_counts":integer_failure_counts}
            if not new_integer_df.empty:
                integer_df = pd.concat([integer_df, new_integer_df], ignore_index=True)

            #Character length
            character_rows = []
            for _,row in join_df[join_df['data_type_source'].isin(['character varying'])].iterrows():
                if row['character_maximum_length_source'] <= row['character_maximum_length_target']:
                    if row['character_maximum_length_source'] < row['character_maximum_length_target']:
                        table_warning = "Yes"
                        r = row[['table_name_source','column_name_source','data_type_source','data_type_target','character_maximum_length_source','character_maximum_length_target']].copy()
                        r['warning'] = "Yes"
                        character_rows.append(r)
                else:
                    character_failure_counts += 1
                    r = row[['table_name_source','column_name_source','data_type_source','data_type_target','character_maximum_length_source','character_maximum_length_target']].copy()
                    r['warning'] = "No"
                    character_rows.append(r)
            new_character_df = pd.DataFrame(character_rows).reset_index() if character_rows else pd.DataFrame()
            if character_failure_counts == 0:
                table_result["character_precision_validation"] = {"status":"PASS"}
            else:
                table_result["character_precision_validation"] = {"status":"FAIL","data":new_character_df,"failure_counts":character_failure_counts}
            if not new_character_df.empty:
                character_df = pd.concat([character_df, new_character_df], ignore_index=True)



            batch_end_time = datetime.now()
            diff_batch = batch_end_time - batch_start_time
            batch_start_time_str = batch_start_time.strftime("%H:%M:%S")
            batch_end_time_str = batch_end_time.strftime("%H:%M:%S")
            total_batch_time_taken = time.strftime("%H:%M:%S", time.gmtime(diff_batch.total_seconds()))

            test_case_status = {test_case: result.get("status") for test_case, result in table_result.items()}
            overall_status = "FAIL" if "FAIL" in test_case_status.values() else "PASS"
            if overall_status == "FAIL":
                failure_count += 1
            create_summary_schema(
                run_at, run_id, validation, table_name, source, None, target, overall_status,
                output_path, test_case_status=test_case_status,
                batch_start_time=batch_start_time_str, batch_end_time=batch_end_time_str,
                diff_batch=total_batch_time_taken, layer_type=layer[0], warning=table_warning)




        except (pyodbc.Error, psycopg2.Error):
            error_message = f"Database/network error for table={table_name} for validation={validation}\n {traceback.format_exc()}"
            logger.error(
                "Database/network error for table=%s validation=%s",
                table_name, validation, exc_info=True
            )
            status = "FAIL"
            system_error = True
            create_summary_schema(
                run_at, run_id, validation, table_name, source, None, target, status,
                output_path, error_message=error_message, layer_type=layer[0]
            )
            continue

        except Exception:
            error_message = f"Unexpected error for table={table_name} for validation={validation}\n {traceback.format_exc()}"
            logger.error(
                "Unexpected error for table=%s validation=%s",
                table_name, validation, exc_info=True
            )
            status = "FAIL"
            system_error = True
            create_summary_schema(
                run_at, run_id, validation, table_name, source, None, target, status,
                output_path, error_message=error_message, layer_type=layer[0]
            )
            continue

    dfs_to_write = {
        "missing_in_source": missing_in_source_df,
        "missing_in_target": missing_in_target_df,
        "datatype_validation": datatype_df,
        "datetime_precision_validation": datetime_df,
        "integer_precision_validation": integer_df,
        "character_precision_validation": character_df,
    }

    writer_kwargs = {
            "engine": "openpyxl",
            "mode": "a" if os.path.exists(filepath) else "w"
            }
    try:
        with pd.ExcelWriter(filepath, **writer_kwargs) as writer:
            for sheet_name, df in dfs_to_write.items():
                if not df.empty:
                    df = df.drop(columns = ['index'])
                    df.to_excel(
                        writer,
                        sheet_name=sheet_name,
                        index=False
                    )
    except Exception as e:
        logger.exception(
        f"Failed to write Excel file: {filepath}"
        )
        raise RuntimeError(
        f"Failed to write Excel file: {filepath}"
        ) from e
    return failure_count, system_error
