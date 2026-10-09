import yaml
from db.factory import get_database
from utils.utility import get_logger
from datetime import datetime
import time
import pyodbc
import psycopg2
import traceback
import os
import pandas as pd

logger = get_logger(__name__)

def create_summary_integrity(run_at,run_id,validation_type,table_name,source_type,status,output_path,test_case=None,summary=None,source_rows=None,output_file_path=None,batch_start_time=None,batch_end_time=None,diff_batch=None,error_message=None,layer_type=None):
    summary_dict = {
        "run_id": run_id,
        "run_at": run_at,
        "test_case": test_case,
        "table_name": table_name,
        "summary": summary,
        "validation_performed": validation_type,
        "source_type": source_type,
        "source_count": source_rows,
        "status": status,
        "output_file_path": output_file_path,
        "batch_start_time": batch_start_time,
        "batch_end_time": batch_end_time,
        "total_time_taken": diff_batch,
        "error_message": error_message
    }

    summary_df = pd.DataFrame([summary_dict])
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

    validation = "integrity_check"
    output_path = outputpaths[validation]
    config_path_yaml = configpaths[validation]
    failure_count = 0
    system_error = False

    logger.info("Processing validation type: %s", validation)
    logger.debug("Output path: %s", output_path)
    logger.debug("Config path: %s", config_path_yaml)

    for yamlfile in config_path_yaml:
        with open(yamlfile) as f:
            config = yaml.safe_load(f)
        logger.info("Loaded configuration: %s", yamlfile)

        if "all" in tables:
            tables_to_process = config["tables"].items()
        else:
            tables_to_process = [
                (table, config["tables"][table]) for table in tables if table in config["tables"]]
            if len(tables_to_process) == 0:
                raise ValueError("No tables found to process.")

        for table_name, table_config in tables_to_process:
            logger.info("Processing table: %s", table_name)

            for validation_name, validation_config in (table_config.get("validation") or table_config["validations"]).items():
                logger.debug("Validation configuration: %s", validation_name)
                source = validation_config.get("source")
                query = validation_config.get("query")
                if environment == "dev":
                    query = query.format(env="DEV")
                elif environment == "qat":
                    query = query.format(env="QAT")
                elif environment == "prod":
                    query = query.format(env="PRD")
                else:
                    query = query.format(env="DEV")
                summary = validation_config.get("summary")
                test_case = validation_config.get("testcase")

                try:
                    batch_start_time = datetime.now()
                    logger.info("Executing integrity check query for table %s", table_name)
                    logger.debug("Query: %s", query)
                    obj = get_database(source, BASE_DIR, environment)
                    df = obj.execute_query(query)

                    row_count = len(df)
                    output_file_path = ""

                    batch_end_time = datetime.now()
                    diff_batch = batch_end_time - batch_start_time
                    batch_start_time_str = batch_start_time.strftime("%H:%M:%S")
                    batch_end_time_str = batch_end_time.strftime("%H:%M:%S")
                    total_batch_time_taken = time.strftime("%H:%M:%S", time.gmtime(diff_batch.total_seconds()))

                    if row_count > 0:
                        status = "FAIL"
                        failure_count += 1
                        logger.info("Current failure count: %s", failure_count)
                        filepath = os.path.join(output_path, f"{table_name}_result.csv")
                        df.to_csv(filepath, index=False)
                        output_file_path = filepath
                        logger.warning(
                            "Integrity check failed for table=%s validation=%s, %s offending rows saved to %s",
                            table_name, validation_name, row_count, filepath
                        )
                    else:
                        status = "PASS"
                        logger.info(
                            "Integrity check passed for table=%s validation=%s",
                            table_name, validation_name
                        )

                    create_summary_integrity(
                        run_at, run_id, validation_name, table_name, source, status,
                        output_path, test_case=test_case, summary=summary, source_rows=row_count,
                        output_file_path=output_file_path,
                        batch_start_time=batch_start_time_str, batch_end_time=batch_end_time_str,
                        diff_batch=total_batch_time_taken, layer_type=layer[0]
                    )

                except (pyodbc.Error, psycopg2.Error):
                    error_message = f"Database/network error for table={table_name} for validation={validation_name}\n {traceback.format_exc()}"
                    logger.error(
                        "Database/network error for table=%s validation=%s",
                        table_name, validation_name, exc_info=True
                    )
                    status = "FAIL"
                    failure_count += 1
                    system_error = True
                    create_summary_integrity(
                        run_at, run_id, validation_name, table_name, source, status,
                        output_path, test_case=test_case, summary=summary, error_message=error_message, layer_type=layer[0]
                    )
                    continue

                except Exception:
                    error_message = f"Unexpected error for table={table_name} for validation={validation_name}\n {traceback.format_exc()}"
                    logger.error(
                        "Unexpected error for table=%s validation=%s",
                        table_name, validation_name, exc_info=True
                    )
                    status = "FAIL"
                    failure_count += 1
                    system_error = True
                    create_summary_integrity(
                        run_at, run_id, validation_name, table_name, source, status,
                        output_path, test_case=test_case, summary=summary, error_message=error_message, layer_type=layer[0]
                    )
                    continue

    return failure_count, system_error