import yaml
from db.factory import get_database
from utils.utility import get_logger, create_summary
from datetime import datetime
import time
import pyodbc
import psycopg2
import traceback
import os
import pandas as pd

logger = get_logger(__name__)

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
    report_pack = ctx.report_pack
    failure_count = 0
    system_error = False

    validation = "data_validation"
    output_path = outputpaths[validation]
    config_path_yaml = configpaths[validation]
    logger.info("Processing validation type: %s", validation)
    logger.debug("Output path: %s", output_path)
    logger.debug("Config path: %s", config_path_yaml)

    env_params = {
        "dev": {"env": "DEV", "schema_env": "DEV"},
        "stg": {"env": "STG", "schema_env": "STAGING"},
        "qat": {"env": "QAT", "schema_env": "QAT"},
        "prod": {"env": "PRD", "schema_env": "PRD"},
    }.get(environment, {"env": "DEV", "schema_env": "DEV"})

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
                if layer[0] == "silver":
                    source_query = source_query.format(**env_params)

                condition = f" WHERE created_date between {from_date} and {to_date}"
                if run_type == "incremental":
                    source_query = (source_query+condition)

                target = validation_config.get("target")
                target_query = validation_config.get("targetquery")
                target_query = target_query.format(**env_params)
                if run_type == "incremental":
                    target_query = (target_query+condition)

                source_table_name = validation_config.get("source_table_name",'')
                target_table_name = validation_config.get("target_table_name",'')
                sourcecolumn = validation_config.get("sourcecolumn",'').lower()
                targetcolumn = validation_config.get("targetcolumn",'').lower()

                report_tile = None
                test_case = None
                summary = None
                if layer[0] == "reports":
                    report_tile = validation_config.get("report_tile")
                    test_case = validation_config.get("test_case")
                    source_table_name = table_name
                    summary = validation_config.get("summary")

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

                    source_rows = len(source_df)
                    target_rows = len(target_df)
                    sourcecolumn = sourcecolumn.split(',')
                    source_df = source_df.set_index(sourcecolumn)
                    targetcolumn = targetcolumn.split(',')
                    target_df = target_df.set_index(targetcolumn)
                    source_df = source_df.sort_values(sourcecolumn)
                    target_df = target_df.sort_values(targetcolumn)
                    output_file_path = ""
                    logger.debug("Source row count: %s", source_rows)
                    logger.debug("Target row count: %s", target_rows)

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
                        create_summary(run_at,run_id,validation_name,source_table_name,source,target_table_name,target,status,output_path,source_rows,target_rows,output_file_path,batch_start_time_str,batch_end_time_str,total_batch_time_taken,layer_type=layer[0],report_pack=report_pack[0] if layer[0] == "reports" else None,report_tile=report_tile,test_case=test_case,summary=summary)

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
                        filepath = os.path.join(output_path,f"{table_name}_result.xlsx")
                        logger.info("Saving mismatch data to %s", filepath)

                        missing_in_source = target_df.index.difference(source_df.index)
                        missing_in_source_df = missing_in_source.to_frame(index=False)
                        missing_in_source = len(missing_in_source.to_list())
                        logger.info("Count of ID's missing_in_source: %s",missing_in_source)
                        missing_in_target = source_df.index.difference(target_df.index)
                        missing_in_target_df = missing_in_target.to_frame(index=False)
                        missing_in_target = len(missing_in_target.to_list())
                        logger.info("Count of ID's missing_in_target: %s",missing_in_target)

                        common_idx = source_df.index.intersection(target_df.index)
                        logger.info("Count of ID's common: %s",len(common_idx.to_list()))

                        logger.debug(
                        "Comparing source and target data for table=%s",table_name)

                        diff_df = (source_df.loc[common_idx].sort_index().compare(target_df.loc[common_idx].sort_index()                    # type: ignore
                        ))
                        mismatch_count = len(diff_df)

                        writer_kwargs = {
                            "engine": "openpyxl",
                            "mode": "a" if os.path.exists(filepath) else "w"
                        }
                        if writer_kwargs["mode"] == "a":
                            writer_kwargs["if_sheet_exists"] = "replace"

                        with pd.ExcelWriter(filepath, **writer_kwargs) as writer:
                            sheets_written = 0
                            columns = set()
                            d = {}
                            if not diff_df.empty:
                                for i in diff_df.columns.to_list():
                                    columns.add(i[0])
                                for col in columns:
                                    col_df =  diff_df.loc[:,col]
                                    col_df = col_df.reset_index()
                                    col_df = col_df[col_df.notna().all(axis=1)]
                                    col_df_count = len(col_df)
                                    col_df.iloc[:2000].to_excel(writer, sheet_name=col, index=False)
                                    sheets_written += 1
                                    d[col] = col_df_count
                                counts_df = pd.DataFrame(d.items(),index=None,columns=['Column Name','Count'])
                                counts_df.to_excel(writer, sheet_name="column_counts", index=False)

                            if not missing_in_source_df.empty:
                                missing_in_source_df.iloc[:2000].to_excel(
                                    writer,
                                    sheet_name="Missing_in_Source",
                                    index=False
                                )
                                sheets_written += 1

                            if not missing_in_target_df.empty:
                                missing_in_target_df.iloc[:2000].to_excel(
                                    writer,
                                    sheet_name="Missing_in_Target",
                                    index=False
                                )
                                sheets_written += 1

                            if sheets_written == 0:
                                pd.DataFrame({"Message": ["No differences found"]}).to_excel(
                                    writer,
                                    sheet_name="Summary",
                                    index=False
                                )

                        logger.info("Creating summary file")
                        batch_end_time = datetime.now()
                        diff_batch = batch_end_time - batch_start_time
                        batch_start_time_str = batch_start_time.strftime("%H:%M:%S")
                        batch_end_time_str = batch_end_time.strftime("%H:%M:%S")
                        total_batch_time_taken = time.strftime("%H:%M:%S",time.gmtime(diff_batch.total_seconds()))
                        create_summary(run_at,run_id,validation_name,table_name,source,target_table_name,target,status,output_path,source_rows,target_rows,filepath,batch_start_time_str,batch_end_time_str,total_batch_time_taken,missing_in_source,missing_in_target,mismatch_count,layer_type=layer[0],report_pack=report_pack[0] if layer[0] == "reports" else None,report_tile=report_tile if layer[0] == "reports" else None,test_case=test_case if layer[0] == "reports" else None,summary=summary if layer[0] == "reports" else None)
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
                    create_summary(run_at,run_id,validation_name,source_table_name,source,target_table_name,target,status,output_path,error_message=error_message,layer_type=layer[0],report_pack=report_pack[0] if layer[0] == "reports" else None,report_tile=report_tile,test_case=test_case,summary=summary)
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
                    create_summary(run_at,run_id,validation_name,source_table_name,source,target_table_name,target,status,output_path,error_message=error_message,layer_type=layer[0],report_pack=report_pack[0] if layer[0] == "reports" else None,report_tile=report_tile,test_case=test_case,summary=summary)
                    continue

    return failure_count, system_error
