import os
import sys
import argparse
import yaml
import time
import pyodbc
import psycopg2
from db.factory import get_database
from utils.utility import (generate_runid,get_config_output_paths,create_summary,get_logger,add_file_handler)
from datetime import datetime
import traceback
import pandas as pd

def main():
    start_time = datetime.now()
    #Generating run id
    run_id,run_at = generate_runid()
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))
    config_path = os.path.join(BASE_DIR,"config")

    #Logging module
    logger = get_logger(__name__)

    #Getting input report names as parameters
    parser = argparse.ArgumentParser()

    parser.add_argument(
    "--layer_type",
    nargs=1,
    required = True,
    choices=["bronze_postgres","bronze_mssql", "silver", "gold", "reports", "sanity"]
    )

    parser.add_argument(
        "--report_pack",
        nargs=1,
        required=False,
        choices=['emanagement','smanagement','egrowth','sgrowth','eperformance','sperformance']
    )

    parser.add_argument(
        "--tables",
        nargs="+",
        required=True
    )

    parser.add_argument(
        "--count_validation",
        nargs=1,
        required=True,
        choices=['yes','no']
    )

    parser.add_argument(
        "--environment",
        nargs=1,
        required=True,
        choices=['dev','stg','qat','prod','local']
    )

    parser.add_argument(
        "--data_validation",
        nargs=1,
        required=True,
        choices=['yes','no']
    )

    args = parser.parse_args()

    layer = args.layer_type
    report_pack = args.report_pack
    tables = args.tables
    environment = args.environment[0]

    if layer[0] == "reports" and args.count_validation[0] == "yes":
        parser.error("--count_validation is not supported for layer_type=reports; only --data_validation is accepted.")


    validation_dirs = []
    if args.count_validation[0] == 'yes':
        validation_dirs.append("count_validation")

    if args.data_validation[0] == 'yes':
        validation_dirs.append("data_validation")

    outputpaths,configpaths,logpath = get_config_output_paths(run_id,layer,report_pack,BASE_DIR,config_path,validation_dirs,tables)
    logger = add_file_handler(
        logger=logger,
        log_directory=logpath,
        log_filename=f"validation_{run_id}.log"
    )

    logger.info("Start Time: %s", start_time.strftime("%Y-%m-%d %H:%M:%S"))
    logger.info("File logging initialized")

    print("="*100)
    logger.info("Validation job started")
    logger.info("Run ID: %s", run_id)

    logger.info(
        "Input parameters - layer=%s, tables=%s, count_validation=%s, data_validation=%s",
        args.layer_type[0],
        args.tables,
        args.count_validation[0],
        args.data_validation[0]
    )

    logger.debug("Validation directories: %s", validation_dirs)

    failure_count = 0
    system_error = False

    #Each validation is process in order
    for validation in validation_dirs:
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

                if layer[0] == "sanity":
                    for validation_name, validation_config in table_config["validation"].items():
                        logger.debug("Validation configuration: %s", validation_name)
                        source = validation_config.get("source")
                        query = validation_config.get("query")
                        if environment == "dev":
                                query = query.format(env = "DEV")
                        elif environment == "qat":
                            query = query.format(env = "QAT")
                        elif environment == "prod":
                            query = query.format(env = "PROD")
                        else:
                            query = query.format(env = "DEV")
                        summary = validation_config.get("summary")
                        test_case = validation_config.get("test_case")

                        try:
                            batch_start_time = datetime.now()
                            logger.info("Executing integrity check query for table %s", table_name)
                            logger.debug("Query: %s", query)
                            obj = get_database(source, BASE_DIR, environment)
                            df = obj.execute_query(query)
           
                            # row_count = df['ORPHAN_OR_NULL_KEYS'][0]
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
                                filepath = os.path.join(output_path, f"{table_name}_{validation_name}_result_{run_id}.csv")
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

                            create_summary(
                                run_at, run_id, validation_name, table_name, source, None, None, status,
                                output_path,test_case=test_case, source_rows=row_count, output_file_path=output_file_path,
                                batch_start_time=batch_start_time_str, batch_end_time=batch_end_time_str,
                                diff_batch=total_batch_time_taken, layer_type=layer[0], summary=summary
                            )

                        except (pyodbc.Error, psycopg2.Error):
                            error_message = f"Database/network error for table={table_name} for validation={validation_name}\n {traceback.format_exc()}"
                            logger.error(
                                "Database/network error for table=%s validation=%s",
                                table_name, validation_name, exc_info=True
                            )
                            system_error = True
                            status = "FAIL"
                            create_summary(
                                run_at, run_id, validation_name, table_name, source, None, None, status,
                                output_path, error_message=error_message, layer_type=layer[0], summary=summary
                            )
                            continue

                        except Exception:
                            error_message = f"Unexpected error for table={table_name} for validation={validation_name}\n {traceback.format_exc()}"
                            logger.error(
                                "Unexpected error for table=%s validation=%s",
                                table_name, validation_name, exc_info=True
                            )
                            status = "FAIL"
                            create_summary(
                                run_at, run_id, validation_name, table_name, source, None, None, status,
                                output_path, error_message=error_message, layer_type=layer[0], summary=summary
                            )
                            continue

                    continue

                for validation_name, validation_config in table_config["validations"].items():
                    logger.debug("Validation configuration: %s", validation_name)
                    source = validation_config.get("source")
                    source_query = validation_config.get("sourcequery")
                    target = validation_config.get("target")
                    target_query = validation_config.get("targetquery")
                    if environment == "dev":
                        target_query = target_query.format(env = "DEV")
                    elif environment == "stg":
                        query = target_query.format(env = "STG")
                    elif environment == "qat":
                        query = target_query.format(env = "QAT")
                    elif environment == "prod":
                        query = target_query.format(env = "PROD")
                    else:
                        query = target_query.format(env = "DEV")
                    source_table_name = validation_config.get("source_table_name")
                    target_table_name = validation_config.get("target_table_name")
                    sourcecolumn = validation_config.get("sourcecolumn",'').lower()
                    targetcolumn = validation_config.get("targetcolumn",'').lower()

                    report_tile = None
                    test_case = None
                    summary = None
                    if layer[0] == "reports":
                        report_tile = validation_config.get("report_tile")
                        test_case = validation_config.get("test_case")
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

                        if validation_name == "count_validation": 
                            source_rows = source_df['source_row_count'].iloc[0]
                            target_rows = target_df['target_row_count'].iloc[0]
                            source_df.columns = ['count']
                            target_df.columns = ['count']
                            logger.debug("Source row count: %s", source_rows)
                            logger.debug("Target row count: %s", target_rows)

                            output_file_path = ""
                        else:
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
                            batch_start_time = batch_start_time.strftime("%H:%M:%S")
                            batch_end_time = batch_end_time.strftime("%H:%M:%S")
                            total_batch_time_taken = time.strftime("%H:%M:%S",time.gmtime(diff_batch.total_seconds()))
                            create_summary(run_at,run_id,validation_name,source_table_name,source,target_table_name,target,status,output_path,source_rows,target_rows,output_file_path,batch_start_time,batch_end_time,total_batch_time_taken,layer_type=layer[0],report_pack=report_pack[0] if layer[0] == "reports" else None,report_tile=report_tile,test_case=test_case,summary=summary)
                                
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
                            filepath = os.path.join(output_path,f"{table_name}_result.xlsx") #change
                            logger.info("Saving mismatch data to %s", filepath)
                            missing_in_source = ""
                            missing_in_target = ""
                            mismatch_count = ""

                            if validation != 'count_validation':
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

                                with pd.ExcelWriter(filepath, engine="openpyxl") as writer:
                                    sheets_written = 0

                                    if not diff_df.empty:
                                        diff_df.iloc[:2000].to_excel(writer, sheet_name="Differences", index=True)
                                        sheets_written += 1

                                    if not missing_in_source_df.empty:
                                        missing_in_source_df.iloc[:2000].to_excel(
                                            writer,
                                            sheet_name="Missing_in_Source",
                                            index=True
                                        )
                                        sheets_written += 1

                                    if not missing_in_target_df.empty:
                                        missing_in_target_df.iloc[:2000].to_excel(
                                            writer,
                                            sheet_name="Missing_in_Target",
                                            index=True
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
                            batch_start_time = batch_start_time.strftime("%H:%M:%S")
                            batch_end_time = batch_end_time.strftime("%H:%M:%S")
                            total_batch_time_taken = time.strftime("%H:%M:%S",time.gmtime(diff_batch.total_seconds()))
                            create_summary(run_at,run_id,validation_name,source_table_name,source,target_table_name,target,status,output_path,source_rows,target_rows,filepath,batch_start_time,batch_end_time,total_batch_time_taken,missing_in_source,missing_in_target,mismatch_count if args.data_validation[0] == 'yes' else None,layer_type=layer[0],report_pack=report_pack[0] if layer[0] == "reports" else None,report_tile=report_tile,test_case=test_case,summary=summary)
                            print("+"*100)


                    except (pyodbc.Error, psycopg2.Error):
                        error_message = (f"Database/network error for table={table_name} for validation={validation_name}\n {traceback.format_exc()}")
                        logger.error(
                            "Database/network error for table=%s validation=%s",
                            table_name,
                            validation_name,
                            exc_info=True
                        )
                        system_error = True
                        status = "FAIL"
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
                        create_summary(run_at,run_id,validation_name,source_table_name,source,target_table_name,target,status,output_path,error_message=error_message,layer_type=layer[0],report_pack=report_pack[0] if layer[0] == "reports" else None,report_tile=report_tile,test_case=test_case,summary=summary)
                        continue

    end_time = datetime.now()
    duration = end_time - start_time
    total_time_taken = time.strftime("%H:%M:%S",time.gmtime(duration.total_seconds()))
    logger.info("Validation job completed")
    logger.info("End Time: %s", end_time.strftime("%Y-%m-%d %H:%M:%S"))
    logger.info("Duration: %s", total_time_taken)
    logger.info("Total failures: %s", failure_count)
    logger.info("Run ID: %s", run_id)
    sys.exit(1 if system_error else 0)

if __name__ == "__main__":
    main()
