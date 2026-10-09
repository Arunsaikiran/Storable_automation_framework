from datetime import datetime
from pathlib import Path
import os
import pandas as pd
import logging
from openpyxl import load_workbook
import sys
import argparse
from dataclasses import dataclass


#Generating runids
def generate_runid():
    return datetime.now().strftime("%Y%m%d_%H%M%S_%f"),datetime.now().strftime("%d-%b-%Y %H:%M:%S.%f")

#Return sql based on load_type[Historical/Incremental]
def generate_sql(load_type,source_query,target_query,from_date,to_date,col):

    if load_type.lower() == 'historical':
        source_query = source_query 
        target_query = target_query 

    elif load_type.lower() == 'incremental':
        incremental_query = f" where {col} between {from_date} and {to_date}"
        source_query = source_query + incremental_query
        target_query = target_query + incremental_query

    return source_query,target_query

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

    elif layer_type[0] in ("bronze_mssql", "bronze_postgres", "silver"):
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
    print(result_file)

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


def create_summary(run_at,run_id,validation_type,source_table_name,source_type,target_table_name,target_type,status,output_path,source_rows=None,target_rows=None,output_file_path=None,batch_start_time=None,batch_end_time=None,diff_batch=None,missing_in_source=None,missing_in_target=None,mismatch_count=None,error_message=None,layer_type=None,report_pack=None,report_tile=None,test_case=None,summary=None):
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

    # if layer_type == 'reports' :
    #     _write_overall_summary_excel(
    #         summary_df=summary_df,
    #         output_path=output_path,
    #         validation_type=validation_type)


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

    # Prevent the same file handler from being added more than once.
    existing_files = {
        Path(handler.baseFilename).resolve()
        for handler in logger.handlers
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

    logger.addHandler(file_handler) 

    logger.info("Log file location: %s", log_file.resolve())

    return logger

def valid_date(date_str):
    try:
        return str(datetime.strptime(date_str, "%Y-%m-%d").date())
    except ValueError:
        raise argparse.ArgumentTypeError(f"Invalid date: {date_str}. Expected format YYYY-MM-DD")

def parse_arguments():
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

    parser.add_argument(
        "--from_date",
        type=valid_date,
        required=False,
        help="Date in YYYY-MM-DD format"
    )

    parser.add_argument(
        "--to_date",
        type=valid_date,
        required=False,
        help="Date in YYYY-MM-DD format"
    )

    parser.add_argument(
        "--run_type",
        required=False,
        help="Run-type Historical/Incremental",
        default='historical',
        choices=['historical','incremental']
    )

    args = parser.parse_args()

    run_type = args.run_type
    layer_type = args.layer_type

    if run_type == "historical":
        if args.from_date or args.to_date:
            parser.error("--from-date and --to-date can only be used with --incremental")
    else:
        if args.from_date is None or args.to_date is None:
            parser.error("Both from and to dates must be provided!")
        
    if layer_type[0] == "reports":
        if args.count_validation[0] == "yes" or args.schema_validation[0] == "yes":
            parser.error("Only the parameter --data_validation = 'yes' is accepted.")

    if args.schema_validation and layer_type[0] not in ("bronze_mssql", "bronze_postgres"):
        parser.error("--schema_validation 'yes' is only allowed when --layer_type is 'bronze_mssql' or 'bronze_postgres'.")

    if layer_type[0] in ("sanity", "reports"):
        if not args.report_pack:
            parser.error("--report_pack is required when --layer_type is 'sanity' or 'reports'.")
    else:
        if args.report_pack:
            parser.error("--report_pack is only allowed when --layer_type is 'sanity' or 'reports'.")

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
        from_date: str | None = None
        to_date: str | None = None
    return DataContext