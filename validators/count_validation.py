import yaml
from db.factory import get_database, close_all_databases
from utils.utility import get_logger
from datetime import datetime
import time
import pyodbc
import psycopg2
import traceback
import os
import pandas as pd

logger = get_logger(__name__)

def create_summary_count(run_at,run_id,validation_type,source_table_name,source_type,target_table_name,target_type,status,output_path,source_rows=None,target_rows=None,batch_start_time=None,batch_end_time=None,diff_batch=None,error_message=None,layer_type=None):
    summary_df = pd.DataFrame([{
        "run_id": run_id,
        "run_at": run_at,
        "validation_performed": validation_type,
        "source_table_name": source_table_name,
        "source_type": source_type,
        "target_table_name": target_table_name,
        "target_type": target_type,
        "source_count": source_rows,
        "target_count": target_rows,
        "count_difference": (abs(int(target_rows) - int(source_rows)) if target_rows is not None and source_rows is not None else None),
        "status": status,
        "batch_start_time": batch_start_time,
        "batch_end_time": batch_end_time,
        "total_time_taken": diff_batch,
        "error_message": error_message
    }])

    summary_file = os.path.join(output_path, f"{validation_type}_summary.csv")

    summary_df.to_csv(summary_file,
        mode="a",
        index=False,
        header=not os.path.exists(summary_file))

def validate(ctx):
    run_id = ctx.run_id
    run_at = ctx.run_at
    layer = ctx.layer_type
    outputpaths = ctx.outputpaths
    configpaths = ctx.configpaths
    BASE_DIR = ctx.BASE_DIR
    environment = ctx.environment
    tables = ctx.tables
    from_date = ctx.from_date
    to_date = ctx.to_date
    run_type = ctx.run_type
    failure_count = 0
    system_error = False

    validation = "count_validation"
    output_path = outputpaths[validation]
    config_path_yaml = configpaths[validation]
    logger.info("Processing validation type: %s", validation)
    logger.debug("Output path: %s", output_path)
    logger.debug("Config path: %s", config_path_yaml)

    for yamlfile in config_path_yaml:
        with open(yamlfile) as f:
            config = yaml.safe_load(f)
        logger.info("Loaded configuration: %s", config_path_yaml)

        if "all" in tables:
            tables_to_process = config["tables"].items()
        else:
            tables_to_process = [
                (table, config["tables"][table]) for table in tables if table in config["tables"]]
            if len(tables_to_process) == 0:
                raise ValueError("No tables found to process.")

        for table_name, table_config in tables_to_process:
            logger.info("Processing table: %s", table_name)

            for validation_name, validation_config in table_config["validations"].items():
                logger.debug("Validation configuration: %s", validation_name)
                source = validation_config.get("source")
                source_query = validation_config.get("sourcequery")
                condition = f" WHERE created_date between {from_date} and {to_date}"
                if run_type == "incremental":
                    source_query = (source_query+condition)
                target = validation_config.get("target")
                target_query = validation_config.get("targetquery")
                if environment == "dev":
                    target_query = target_query.format(env = "DEV",schema_env= "DEV")
                elif environment == "stg":
                    target_query = target_query.format(env = "STG",schema_env= "STAGING")
                elif environment == "qat":
                    target_query = target_query.format(env = "QAT")
                elif environment == "prod":
                    target_query = target_query.format(env = "PRD")
                else:
                    target_query = target_query.format(env = "DEV")
                if run_type == "incremental":
                    target_query = (target_query+condition)
                source_table_name = validation_config.get("source_table_name",'')
                target_table_name = validation_config.get("target_table_name",'')
                try:
                    batch_start_time = datetime.now()
                    #source
                    logger.info("Executing source query for table %s", table_name)
                    logger.debug("Source query: %s", source_query)
                    obj = get_database(source,BASE_DIR,environment)
                    source_df = obj.execute_query(source_query)
                    source_df = source_df.astype(str)

                    #target
                    logger.info("Executing target query for table %s", table_name)
                    logger.debug("Target query: %s", target_query)
                    obj = get_database(target,BASE_DIR,environment)
                    target_df = obj.execute_query(target_query)
                    target_df = target_df.astype(str)

                    source_df.columns = source_df.columns.str.strip().str.lower()
                    target_df.columns = target_df.columns.str.strip().str.lower()

                    source_rows = source_df['source_row_count'].iloc[0]
                    target_rows = target_df['target_row_count'].iloc[0]
                    source_df.columns = ['count']
                    target_df.columns = ['count']
                    logger.debug("Source row count: %s", source_rows)
                    logger.debug("Target row count: %s", target_rows)

                    #Comparison
                    if source_df.equals(target_df):
                        logger.info("Match/Mismatch: Match")
                        status = "PASS"
                        logger.info(
                        "Validation passed for table=%s for the validation=%s",
                        table_name,
                        validation_name
                        )
                        logger.info("Creating summary file")
                        batch_end_time = datetime.now()
                        diff_batch = batch_end_time - batch_start_time
                        batch_start_time_str = batch_start_time.strftime("%H:%M:%S")
                        batch_end_time_str = batch_end_time.strftime("%H:%M:%S")
                        total_batch_time_taken = time.strftime("%H:%M:%S",time.gmtime(diff_batch.total_seconds()))
                        create_summary_count(run_at,run_id,validation_name,source_table_name,source,target_table_name,target,status,output_path,source_rows,target_rows,batch_start_time_str,batch_end_time_str,total_batch_time_taken,layer_type=layer[0])

                    else:
                        logger.info("Match/Mismatch: Mismatch")
                        status = "FAIL"
                        logger.warning(
                        "Validation failed for table=%s validation=%s",
                        table_name,
                        validation_name
                        )
                        failure_count += 1
                        logger.info("Current failure count: %s", failure_count)

                        logger.info("Creating summary file")
                        batch_end_time = datetime.now()
                        diff_batch = batch_end_time - batch_start_time
                        batch_start_time_str = batch_start_time.strftime("%H:%M:%S")
                        batch_end_time_str = batch_end_time.strftime("%H:%M:%S")
                        total_batch_time_taken = time.strftime("%H:%M:%S",time.gmtime(diff_batch.total_seconds()))
                        create_summary_count(run_at,run_id,validation_name,source_table_name,source,target_table_name,target,status,output_path,source_rows,target_rows,batch_start_time_str,batch_end_time_str,total_batch_time_taken,layer_type=layer[0])
                        print("+"*100)

                except (pyodbc.Error, psycopg2.Error):
                    error_message = (f"Database/network error for table={table_name} for validation={validation_name}\n {traceback.format_exc()}")
                    logger.error(
                        "Database/network error for table=%s validation=%s",
                        table_name,
                        validation_name,
                        exc_info=True
                    )
                    status = "FAIL"
                    failure_count += 1
                    system_error = True
                    create_summary_count(run_at,run_id,validation_name,source_table_name,source,target_table_name,target,status,output_path,error_message=error_message,layer_type=layer[0])
                    continue

                except Exception:
                    error_message = (f"Unexpected error for table={table_name} for validation={validation_name}\n {traceback.format_exc()}")
                    logger.error(
                        "Unexpected error for table=%s validation=%s",
                        table_name,
                        validation_name,
                        exc_info=True
                    )
                    status = "FAIL"
                    failure_count += 1
                    system_error = True
                    create_summary_count(run_at,run_id,validation_name,source_table_name,source,target_table_name,target,status,output_path,error_message=error_message,layer_type=layer[0])
                    continue

    return failure_count, system_error
