from datetime import datetime
from pathlib import Path
import os
import pandas as pd
import logging
from openpyxl import load_workbook
import sys
import argparse
from dataclasses import dataclass
import yaml
import requests
from utils.exceptions import NoNewDataError,NoNewSyncError

# CHANGE: YAML generation. The bronze layers that have YAML generation, filter workbooks and schema validation. Every layer
# check below and in main.py reads this one tuple, so a new bronze layer (bronze_mysql) is added here once, not at each check.
BRONZE_LAYERS = ("bronze_postgres", "bronze_mssql", "bronze_newbook")


#Generating runids
def generate_runid():
    return datetime.now().strftime("%Y%m%d_%H%M%S_%f"),datetime.now().strftime("%d-%b-%Y %H:%M:%S.%f")

#Function to read the correct configuration file based on the parameters passed
def get_config_output_paths(run_id,layer_type,report_pack,base_dir,config_path,validation_dirs,table_list):
    outputpaths = {}
    configpaths = {}
    logpath = None
    output_dir = os.path.join(base_dir, "output")
    if layer_type[0] == "reports" or layer_type[0] == "sanity":
        logpath = os.path.join(
        output_dir,
        layer_type[0],
        report_pack[0],
        f"validation_{run_id}"
        )

        os.makedirs(output_dir, exist_ok=True)

        if layer_type[0] == "sanity":
            integrity_yaml = os.path.join(
                config_path,
                layer_type[0],
                "integrity_check.yaml"
            )
            # CHANGE: integrity_check.yaml only exists for some run_types
            # (e.g. incremental). Only wire up the path when the config is
            # actually there, so historical sanity runs can still proceed
            # with just the requested data_validation/count_validation.
            if os.path.exists(integrity_yaml):
                integrity_path = os.path.join(
                    output_dir,
                    layer_type[0],
                    f"validation_{run_id}",
                    f"integrity_check_{run_id}"
                )
                os.makedirs(integrity_path, exist_ok=True)
                outputpaths["integrity_check"] = integrity_path
                configpaths["integrity_check"] = [integrity_yaml]

        for validation in validation_dirs:
                if validation == 'integrity_check':
                    path = os.path.join(
                        output_dir,
                        layer_type[0],
                        report_pack[0],
                        f"validation_{run_id}",
                        f"{validation}_{run_id}"
                    )
                    integrity_dir = os.path.join(config_path, layer_type[0], report_pack[0], validation)

                    yaml_paths = []
                    if 'all' in table_list:
                        for table in os.listdir(integrity_dir):
                            yaml_paths.append(os.path.join(integrity_dir, table))
                    else:
                        for table in table_list:
                            yaml_paths.append(os.path.join(integrity_dir, f"{table}.yaml"))

                    os.makedirs(path, exist_ok=True)
                    outputpaths[validation] = path
                    configpaths[validation] = yaml_paths

                if validation == 'count_validation':
                    path = os.path.join(
                        output_dir,
                        layer_type[0],
                        report_pack[0],
                        f"validation_{run_id}",
                        f"{validation}_{run_id}" 
                    )
        
                    yamlpath = os.path.join(
                        config_path,
                        layer_type[0],
                        report_pack[0],
                        validation,
                        f"{layer_type[0]}.yaml"
                    )
                    
                    os.makedirs(path, exist_ok=True)
                    outputpaths[validation] = path
                    configpaths[validation] = [yamlpath]
        
                if validation == 'data_validation':
                    path = os.path.join(
                        output_dir,
                        layer_type[0],
                        report_pack[0],
                        f"validation_{run_id}",
                        f"{validation}_{run_id}" 
                    )
        
                    yaml_paths = []
                    if 'all' in table_list:
                        all_configs = (os.listdir(os.path.join(config_path, layer_type[0],report_pack[0], validation)))
                        for table in all_configs:
                            yaml_paths.extend([(os.path.join(config_path, layer_type[0],report_pack[0], validation,f"{table}"))])

                    else:
                        for table in table_list:
                            yaml_paths.extend([(os.path.join(config_path, layer_type[0],report_pack[0], validation,f"{table}.yaml"))])
        
                    configpaths[validation] = yaml_paths
                    outputpaths[validation] = path
                    
                    os.makedirs(path, exist_ok=True)

    # CHANGE: YAML generation. The bronze layers come from BRONZE_LAYERS (top of the file); silver keeps the same output paths.
    elif layer_type[0] in BRONZE_LAYERS + ("silver",):
        logpath = os.path.join(
            output_dir,
            layer_type[0],
            f"validation_{run_id}"
        )
        os.makedirs(output_dir, exist_ok=True)
        #Creating directories based on parameters passed
        for validation in validation_dirs:
            if validation == 'schema_validation':
                path = os.path.join(
                    output_dir,
                    layer_type[0],
                    f"validation_{run_id}",
                    f"{validation}_{run_id}" 
                )

                yamlpath = os.path.join(
                    config_path,
                    layer_type[0],
                    validation,
                    f"{validation}.yaml"
                )

                os.makedirs(path, exist_ok=True)
                outputpaths[validation] = path
                configpaths[validation] = [yamlpath]


            elif validation == 'count_validation':
                path = os.path.join(
                    output_dir,
                    layer_type[0],
                    f"validation_{run_id}",
                    f"{validation}_{run_id}" 
                )

                yamlpath = os.path.join(
                    config_path,
                    layer_type[0],
                    validation,
                    f"{layer_type[0]}.yaml"
                )
                
                os.makedirs(path, exist_ok=True)
                outputpaths[validation] = path
                configpaths[validation] = [yamlpath]

            elif validation == 'data_validation':
                path = os.path.join(
                    output_dir,
                    layer_type[0],
                    f"validation_{run_id}",
                    f"{validation}_{run_id}" 
                )

                yaml_paths = []
                if 'all' in table_list:
                    all_configs = (os.listdir(os.path.join(base_dir, config_path, layer_type[0], validation)))
                    for table in all_configs:
                        yaml_paths.extend([(os.path.join(base_dir,config_path, layer_type[0], validation,f"{table}"))])

                else:
                    for table in table_list:
                        yaml_paths.extend([(os.path.join(base_dir, config_path, layer_type[0], validation,f"{table}.yaml"))])

                configpaths[validation] = yaml_paths
                outputpaths[validation] = path
                
                os.makedirs(path, exist_ok=True)

    return outputpaths,configpaths,logpath

def _write_summary_to_table_result_excel(summary_df, output_path, table_name):
    """
    Replicate the row produced by create_summary() into:
        <table_name>_result.xlsx
    as:
        <table_name>_summary

    The existing <validation_type>_summary.csv behavior is intentionally
    preserved. This helper only adds the Excel-sheet copy.
    """
    if not table_name:
        return

    sheet_name = f"{table_name}_summary"[:31]
    result_file = os.path.join(output_path, f"{table_name}_result.xlsx")
    os.makedirs(output_path, exist_ok=True)

    try:
        if os.path.exists(result_file):
            with pd.ExcelWriter(
                result_file,
                engine="openpyxl",
                mode="a",
                if_sheet_exists="replace"
            ) as writer:
                summary_df.to_excel(writer, sheet_name=sheet_name, index=False)
        else:
            with pd.ExcelWriter(result_file, engine="openpyxl") as writer:
                summary_df.to_excel(writer, sheet_name=sheet_name, index=False)

    except Exception:
        logging.getLogger(__name__).exception(
            "Unable to write table summary sheet for table=%s to %s",
            table_name,
            result_file
        )

def _write_overall_summary_excel(summary_df, output_path, validation_type):
    result_file = os.path.join(output_path, f"{validation_type}_result_summary.xlsx")

    os.makedirs(output_path, exist_ok=True)

    try:
        if os.path.exists(result_file):
            existing_df = pd.read_excel(result_file, sheet_name="summary")
            combined_df = pd.concat([existing_df, summary_df], ignore_index=True)
            with pd.ExcelWriter(
                result_file,
                engine="openpyxl",
                mode="a",
                if_sheet_exists="replace"
            ) as writer:
                combined_df.to_excel(writer, sheet_name="summary", index=False)
        else:
            with pd.ExcelWriter(result_file, engine="openpyxl") as writer:
                summary_df.to_excel(writer, sheet_name="summary", index=False)

    except Exception:
        logging.getLogger(__name__).exception(
            "Unable to write overall summary for validation_type=%s to %s",
            validation_type,
            result_file
        )

def create_summary_schema(run_at,run_id,validation_type,source_table_name,source_type,target_table_name,target_type,status,output_path,test_case_status=None,batch_start_time=None,batch_end_time=None,diff_batch=None,error_message=None,layer_type=None,warning=None):
    if validation_type == "schema_validation":

        summary_dict = {
            "run_id": run_id,
            "validation_performed": validation_type,
            "table_name": source_table_name,
            "source_type": source_type,
            "target_type": target_type,
        }

        if test_case_status:
            summary_dict.update(test_case_status)

        summary_dict.update({
            "overall_status": status,
            "warning": warning,
            "batch_start_time": batch_start_time,
            "batch_end_time": batch_end_time,
            "total_time_taken": diff_batch,
            "run_at": run_at,
            "error_message": error_message,
        })

        result_file = os.path.join(
            output_path,
            f"schema_validation_result_summary.xlsx"
        )

        new_df = pd.DataFrame([summary_dict])

        if os.path.exists(result_file):
            existing_df = pd.read_excel(result_file, sheet_name="summary")
            summary_df = pd.concat(
                [existing_df, new_df],
                ignore_index=True
            )
        else:
            summary_df = new_df

        os.makedirs(output_path, exist_ok=True)

        if os.path.exists(result_file):
            with pd.ExcelWriter(
                result_file,
                engine="openpyxl",
                mode="a",
                if_sheet_exists="replace"
            ) as writer:
                summary_df.to_excel(writer, sheet_name="summary", index=False)
        else:
            with pd.ExcelWriter(result_file, engine="openpyxl") as writer:
                summary_df.to_excel(writer, sheet_name="summary", index=False)


def create_summary(run_at,run_id,validation_type,source_table_name,source_type,target_table_name,target_type,status,output_path,source_rows=None,target_rows=None,output_file_path=None,batch_start_time=None,batch_end_time=None,diff_batch=None,missing_in_source=0,missing_in_target=0,mismatch_count=0,error_message=None,layer_type=None,report_pack=None,report_tile=None,test_case=None,summary=None):
    summary_dict = {
        "run_id": run_id,
        "run_at": run_at,
        "validation_performed": validation_type,
        "source_table_name": source_table_name,
        "source_type": source_type,
        "target_table_name": target_table_name,
        "target_type": target_type,
        "source_count": source_rows,
        "missing_in_source": missing_in_source,
        "target_count": target_rows,
        "missing_in_target": missing_in_target,
        "mismatch_count":mismatch_count,
        "status": status,
        "output_file_path":output_file_path,
        "batch_start_time": batch_start_time,
        "batch_end_time": batch_end_time,
        "total_time_taken": diff_batch,
        "error_message": error_message
    }

    if layer_type == 'reports':
        summary_dict = {
            "run_id": run_id,
            "run_at": run_at,
            "report_pack": report_pack,
            "report_tile": report_tile,
            "test_case": test_case,
            "summary": summary,
            "validation_performed": validation_type,
            "source_type": source_type,
            "target_type": target_type,
            "source_count": source_rows,
            "missing_in_source": missing_in_source,
            "target_count": target_rows,
            "missing_in_target": missing_in_target,
            "mismatch_count":mismatch_count,
            "status": status,
            "output_file_path":output_file_path,
            "batch_start_time": batch_start_time,
            "batch_end_time": batch_end_time,
            "total_time_taken": diff_batch,
            "error_message": error_message
        }

    summary_df = pd.DataFrame([summary_dict])

    _write_summary_to_table_result_excel(
    summary_df=summary_df,
    output_path=output_path,
    table_name=source_table_name)


    _write_overall_summary_excel(
            summary_df=summary_df,
            output_path=output_path,
            validation_type=validation_type)

def get_logger(name: str) -> logging.Logger:
    logger = logging.getLogger(name)

    if logger.handlers:
        return logger

    logger.setLevel(logging.DEBUG)

    formatter = logging.Formatter(
        "%(asctime)s | %(levelname)s | %(name)s | %(filename)s:%(lineno)d | %(message)s"
    )

    # Console Handler
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(logging.DEBUG)
    console_handler.setFormatter(formatter)

    logger.addHandler(console_handler)

    return logger

def add_file_handler(
    logger: logging.Logger,
    log_directory: str,
    log_filename:str ="validation.log") -> logging.Logger:

    log_file = Path(os.path.join(log_directory,log_filename))

    # Every module has its own logger (get_logger(__name__)), so the file handler goes on the root logger;
    # their records propagate to it and all land in the same file.
    root_logger = logging.getLogger()

    # Prevent the same file handler from being added more than once.
    existing_files = {
        Path(handler.baseFilename).resolve()
        for handler in root_logger.handlers
        if isinstance(handler, logging.FileHandler)
    }

    if log_file.resolve() in existing_files:
        return logger

    formatter = logging.Formatter(
        "%(asctime)s | %(levelname)s | %(name)s | "
        "%(filename)s:%(lineno)d | %(message)s"
    )

    file_handler = logging.FileHandler(
        filename=log_file,
        mode="a",
        encoding="utf-8"
    )
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(formatter)

    root_logger.addHandler(file_handler)

    logger.info("Log file location: %s", log_file.resolve())

    return logger

logger = get_logger(__name__)

def parse_arguments():
    parser = argparse.ArgumentParser()

    parser.add_argument(
    "--layer_type",
    nargs=1,
    required = True,
    # CHANGE: YAML generation. The bronze choices come from BRONZE_LAYERS, so a new bronze layer is accepted once it is added there.
    choices=[*BRONZE_LAYERS, "silver", "gold", "reports", "sanity"]
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

    parser.add_argument(
        "--schema_validation",
        nargs=1,
        required=True,
        choices=['yes','no']
    )

    parser.add_argument(
        "--integrity_check",
        nargs=1,
        required=False,
        default=['no'],
        choices=['yes','no']
    )

    # CHANGE: YAML generation. 'yes' checks the YAML for the requested tables against the live source and Snowflake structure
    # before the validators run, and rewrites only a YAML whose content changed (UNCHANGED is left alone). 'no' validates the YAML
    # already in config/, and only creates a missing one. The default is 'yes' for bronze_postgres and bronze_mssql, and 'no'
    # for every other layer (they have no generator); it is filled in after parsing, below.
    parser.add_argument(
        "--yaml_generation",
        nargs=1,
        required=False,
        default=None,
        choices=['yes','no']
    )

    # CHANGE: YAML generation. --source_schema, --target_database and --target_schema were removed. Each table's application,
    # source schema, Snowflake database and Snowflake schema are read from its row in the filter workbook
    # (yaml_generation/master_files/), the one source of truth, so a run needs fewer arguments.

    # CHANGE: YAML generation. 'yes' applies the row filters in the filter workbooks to the generated YAML: the historical column
    # for --run_type historical, the incremental column for --run_type incremental. A filtered table is then a part of the big
    # table, so it fits in memory. 'no' loads every row (the workbook is still read for where each table lives). The default is
    # 'yes' for the two bronze layers and 'no' for the others, like --yaml_generation.
    parser.add_argument(
        "--filter_files",
        nargs=1,
        required=False,
        default=None,
        choices=['yes','no']
    )

    parser.add_argument(
        "--run_type",
        required=False,
        help="Run-type Historical/Incremental",
        default='historical',
        choices=['historical','incremental']
    )

    args = parser.parse_args()

    layer_type = args.layer_type

    if layer_type[0] == "reports":
        if args.count_validation[0] == "yes" or args.schema_validation[0] == "yes":
            parser.error("Only the parameter --data_validation = 'yes' is accepted.")

    # CHANGE: YAML generation. Schema validation reads the master workbook's type mapping sheet, which only the BRONZE_LAYERS have.
    if args.schema_validation[0] == "yes" and layer_type[0] not in BRONZE_LAYERS:
        parser.error(f"--schema_validation 'yes' is only allowed when --layer_type is one of {', '.join(BRONZE_LAYERS)}.")

    if layer_type[0] in ("sanity", "reports"):
        if not args.report_pack:
            parser.error("--report_pack is required when --layer_type is 'sanity' or 'reports'.")
    else:
        if args.report_pack:
            parser.error("--report_pack is only allowed when --layer_type is 'sanity' or 'reports'.")

    if args.integrity_check is True and layer_type[0] not in ("sanity"):
        parser.error("--integrity_check is only allowed when --layer_type is 'sanity'.")

    # CHANGE: YAML generation. The removed rule required --filter_files 'yes' for an incremental run with --yaml_generation 'yes',
    # because the generated YAML had no window. The generated queries now hold the incremental window (:start_time, :end_time),
    # so an incremental run no longer needs the filter workbook.

    # CHANGE: YAML generation. The defaults of --yaml_generation and --filter_files: 'yes' for the two bronze layers, which have a
    # generator and filter workbooks, and 'no' for every other layer, so a silver, reports or sanity run is unchanged. A value
    # given on the command line is kept, so the checks below still refuse an explicit 'yes' on another layer.
    for flag in ("yaml_generation", "filter_files"):
        if getattr(args, flag) is None:
            setattr(args, flag, ['yes' if layer_type[0] in BRONZE_LAYERS else 'no'])

    # CHANGE: YAML generation. The generator reads the source and Snowflake catalogues of the layer's own database. Only the two
    # bronze layers have a generator (yaml_generation/db_rules.py).
    if args.yaml_generation[0] == "yes" and layer_type[0] not in BRONZE_LAYERS:
        parser.error(f"--yaml_generation 'yes' is only allowed when --layer_type is one of {', '.join(BRONZE_LAYERS)}.")

    # CHANGE: YAML generation. The filter workbooks hold rows for the bronze engines only (the source_database column in
    # yaml_generation/filter_files.py). Any other layer would get no filter, so an incremental run would compare whole tables.
    if args.filter_files[0] == "yes" and layer_type[0] not in BRONZE_LAYERS:
        parser.error(f"--filter_files 'yes' is only allowed when --layer_type is one of {', '.join(BRONZE_LAYERS)} (the filter workbooks are for those).")

    # CHANGE: YAML generation. These flags are used whenever a YAML is written: with --yaml_generation 'yes', and without it
    # when a named table has no YAML yet (main.py creates it). So they are accepted with either value of --yaml_generation.

    return args

def data_context():
    @dataclass
    class DataContext:
        run_id: str
        run_at: str
        validation_dirs: list
        layer_type: list
        outputpaths: dict
        configpaths: dict
        BASE_DIR: str
        environment: str
        tables: list
        run_type: str = 'historical'
        report_pack: list | None = None
    return DataContext

def check_freshness(source_conn,target_conn,source_table_name,target_table_name,layer_type,environment,threshold:int=50):
    freshness_check = 'Pass'
    if layer_type == "bronze_postgres":
        source_query = f"""
        SELECT max(updated_at) as max_date from {source_table_name}
            """
        target_query = f"""
            SELECT max(updated_at) as max_date from {environment}_EDGE_BRONZE.STOREDGE_FMS_PUBLIC.{target_table_name}
            """
    elif layer_type == "bronze_mssql":
        source_query = f"""
        SELECT max(dUpdated) as max_date from {source_table_name}
            """
        target_query = f"""
            SELECT max(dUpdated) as max_date from {environment}_SITELINK_BRONZE.AWSDEV_DEVT5000_DBO.{target_table_name}
            """

    elif layer_type == "bronze_newbook":
        source_query = f"""
        SELECT max(updated_at) as max_date from {environment}_NEWBOOK_AU_BRONZE.NB_DEV_AU_SQL01_CORPORATIONS_11144.{target_table_name}
            """
        target_query = f"""
            SELECT max(updated_at) as max_date from {environment}_NEWBOOK_AU_BRONZE.NB_DEV_AU_SQL01_CORPORATIONS_11144.{target_table_name}
            """
    logger.info("Performing Freshness Check")   
    logger.info("Threshold: %s",threshold) 
    logger.info("Source Query: %s",source_query)
    source_df = source_conn.execute_query(source_query)
    logger.info("Target Query: %s",target_query)
    target_df = target_conn.execute_query(target_query)
    logger.info("Source date: %s",source_df['max_date'].iloc[0])
    logger.info("Target date: %s",target_df['MAX_DATE'].iloc[0])
    hours = (source_df['max_date'] - target_df['MAX_DATE']).dt.total_seconds() / 3600
    value = hours.iloc[0]
    logger.info("Hours: %.2f",value)
    if pd.isna(value) or value > threshold:
        freshness_check = 'Fail'
    return freshness_check

def get_dates(base_dir,validation,table_name,layer,target_conn,target_table_name,environment):
    control_file = os.path.join(base_dir,'control_table.csv')
    date_column = 'data_validation_last_run_date' if validation == 'data' else 'count_validation_last_run_date'

    control_df = pd.read_csv(control_file)
    rows = (control_df[(control_df['table'] == table_name.lower()) & (control_df['layer'] == layer)][date_column])

    if rows.empty:
        raise ValueError(f"No {date_column} found for table '{table_name.lower()}' in {control_file}; seed a value first")
    previous_sync_date = rows.iloc[0]

    if layer == 'bronze_postgres':
        target_table = f"{environment}_EDGE_BRONZE.STOREDGE_FMS_PUBLIC.{target_table_name}"
        col = 'updated_at'
    elif layer == 'bronze_mssql':
        target_table = f"{environment}_SITELINK_BRONZE.AWSDEV_DEVT5000_DBO.{target_table_name}"
        col = 'dUpdated'
    else:
        raise ValueError(f"Unsupported layer '{layer}' for get_dates")

    target_query_sync = f"""
    SELECT max(_fivetran_synced) as MAX_FIVETRAN_SYNCED
    FROM {target_table}
    WHERE _FIVETRAN_ACTIVE = TRUE
    """
    logger.info("Target query: %s",target_query_sync)

    target_df = target_conn.execute_query(target_query_sync)
    max_ts = pd.Timestamp(target_df['MAX_FIVETRAN_SYNCED'].iloc[0])

    if pd.isna(max_ts):
        raise NoNewDataError(f"No active rows for {table_name.lower()} ({layer})")

    if pd.notna(previous_sync_date) and max_ts <= pd.Timestamp(previous_sync_date):
        raise NoNewSyncError(f"No new sync for {table_name.lower()} ({layer}): max_fivetran_synced {max_ts} <= previous_sync_date {previous_sync_date}")

    max_fivetran_synced = _format_ts(max_ts)
    logger.info(f"Table '{table_name}' ({layer}): max_fivetran_synced={max_fivetran_synced}, previous_sync_date={previous_sync_date}")

    #Extracting min/max dUpdated for the sync window

    target_query_updated = f"""
    SELECT max({col}) as MAX_UPDATED,min({col}) as MIN_UPDATED
    FROM {target_table}
    WHERE _FIVETRAN_ACTIVE = TRUE AND _FIVETRAN_SYNCED > '{previous_sync_date}' and _fivetran_synced <= '{max_fivetran_synced}'
    """
    logger.info("Target query: %s",target_query_updated)
    target_df = target_conn.execute_query(target_query_updated)

    if pd.isna(target_df['MAX_UPDATED'].iloc[0]) or pd.isna(target_df['MIN_UPDATED'].iloc[0]):
        raise NoNewDataError(f"No new data for {table_name.lower()} ({layer}) between {previous_sync_date} and {max_fivetran_synced}")

    max_updated = _format_ts(pd.Timestamp(target_df['MAX_UPDATED'].iloc[0]))
    min_updated = _format_ts(pd.Timestamp(target_df['MIN_UPDATED'].iloc[0]))
    logger.info(f"Table '{table_name}' ({layer}): max_updated={max_updated}, min_updated={min_updated}")

    return max_updated,min_updated,max_fivetran_synced

def _format_ts(ts):
    return (ts.strftime('%Y-%m-%d %H:%M:%S.%f')[:-3] + ' ' + ts.strftime('%z')).strip()

#Old
# def get_dates(base_dir,validation,table_name,layer,target_conn,target_table_name,enviroment):

#     yaml_path = os.path.join(base_dir,"creds","api.yaml")
#     control_file = os.path.join(base_dir,'control_table.csv')
#     apikey, secret, connection_id = get_api_creds(yaml_path, layer)

#     url = f"https://api.fivetran.com/v1/connections/{connection_id}"
#     response = requests.get(
#         url,
#         auth=(apikey, secret),
#     )

#     response.raise_for_status()
#     d = response.json()
#     dt = datetime.fromisoformat(d['data']['succeeded_at'])
#     sync_date = dt.strftime('%Y-%m-%d %H:%M:%S.%f')[:-3] + ' ' + dt.strftime('%z')
#     date_column = 'data_validation_last_run_date' if validation == 'data' else 'count_validation_last_run_date'
#     control_df = pd.read_csv(control_file)
#     rows = (control_df[(control_df['table'] == table_name.lower()) & (control_df['layer'] == layer)][date_column])

#     if rows.empty:
#         raise ValueError(f"No {date_column} found for table '{table_name.lower()}' in {control_file}; seed a value first")
#     previous_sync_date = rows.iloc[0]
#     logger.info("Fetching Sync Date and Fivetran Start Date ranges (MIN/MAX)") 
#     logger.info(f"Table '{table_name.lower()}' ({layer}): sync_date={sync_date}, previous_sync_date={previous_sync_date}")

#     if sync_date == previous_sync_date:
#         raise NoNewSyncError(f"No new sync for {table_name.lower()} ({layer}): sync_date {sync_date} equals previous_sync_date")

#     if layer == 'bronze_postgres':
#         target_query = f"""
#         SELECT min(_fivetran_start) as min_fivetran_start ,max(_fivetran_start) as max_fivetran_start
#         FROM {enviroment}_EDGE_BRONZE.STOREDGE_FMS_PUBLIC.{target_table_name}
#         WHERE _FIVETRAN_ACTIVE = TRUE
#         and  _fivetran_synced >= '{previous_sync_date}' and _fivetran_synced < '{sync_date}';
#         """
#     elif layer == 'bronze_mssql':
#         target_query = f"""
#         SELECT min(_fivetran_start) as min_fivetran_start ,max(_fivetran_start) as max_fivetran_start
#         FROM {enviroment}_SITELINK_BRONZE.AWSDEV_DEVT5000_DBO.{target_table_name}
#         WHERE _FIVETRAN_ACTIVE = TRUE
#         and  _fivetran_synced >= '{previous_sync_date}' and _fivetran_synced < '{sync_date}';
#         """

#     logger.info("Target query: %s",target_query)

#     target_df =  target_conn.execute_query(target_query)
#     if pd.isna(target_df['MIN_FIVETRAN_START'].iloc[0]) or pd.isna(target_df['MAX_FIVETRAN_START'].iloc[0]):
#         logger.info(f"No new data has been ingested for this time period: table '{table_name.lower()}' ({layer}), {previous_sync_date} to {sync_date}")
#         #update_control_table(control_file, table_name, layer, validation, sync_date)
#         raise NoNewDataError(f"No new data for {table_name.lower()} ({layer}) between {previous_sync_date} and {sync_date}")
#     min_ts = pd.Timestamp(target_df['MIN_FIVETRAN_START'].iloc[0])
#     max_ts = pd.Timestamp(target_df['MAX_FIVETRAN_START'].iloc[0])

#     min_fivetran_start = (min_ts.strftime('%Y-%m-%d %H:%M:%S.%f')[:-3] + ' ' + min_ts.strftime('%z'))
#     max_fivetran_start = (max_ts.strftime('%Y-%m-%d %H:%M:%S.%f')[:-3] + ' ' + max_ts.strftime('%z'))
#     if layer == 'bronze_mssql':
#         import re
#         max_fivetran_start = re.sub(r'\+0000$', '', max_fivetran_start)
#         min_fivetran_start = re.sub(r'\+0000$', '', min_fivetran_start)

#     logger.info(f"Table '{table_name}' ({layer}): max_fivetran_start={max_fivetran_start}, min_fivetran_start={min_fivetran_start}")
    
#     return max_fivetran_start,min_fivetran_start,sync_date

def update_control_table(file_location, table_name, layer, validation, sync_date):
    date_column = (
        'data_validation_last_run_date'
        if validation == 'data'
        else 'count_validation_last_run_date'
    )
    control_df = pd.read_csv(file_location, dtype=str)
    mask = (
        (control_df['table'] == table_name.lower()) &
        (control_df['layer'] == layer)
    )

    if not mask.any():
        logger.info(f"No control table row for {table_name.lower()}-{layer}; nothing updated")
        return

    updates = {date_column: sync_date}
    for column, value in updates.items():
        if column not in control_df.columns:
            logger.info(f"Column '{column}' not in control table; skipped")
            continue
        control_df.loc[mask, column] = str(value)
    control_df.to_csv(file_location, index=False)

def get_incremental_dates(base_dir,source_conn,target_conn,source_table_name,target_table_name,layer_type,environment,validation):
    if check_freshness(source_conn,target_conn,source_table_name,target_table_name,layer_type,environment) == "Fail":
        raise RuntimeError(f"Freshness check failed for {target_table_name}")
    return get_dates(base_dir,validation,target_table_name,layer_type,target_conn,target_table_name,environment)

def get_api_creds(path, layer):
    with open(path, "r") as f:
        data = yaml.safe_load(f)

    try:
        return data["apikey"], data["secret"], data[layer]
    except KeyError as e:
        raise KeyError(f"Missing key in YAML: {e}")
