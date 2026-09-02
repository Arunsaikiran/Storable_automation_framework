from datetime import datetime
from pathlib import Path
import os
import pandas as pd
import logging
import sys


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

    output_dir = os.path.join(base_dir, "output")
    if layer_type[0] == "reports":
        logpath = os.path.join(
        output_dir,
        layer_type[0],
        report_pack[0],
        f"validation_{run_id}"
        )

        os.makedirs(output_dir, exist_ok=True)
        for validation in validation_dirs:
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
                        all_configs = (os.listdir(os.path.join(base_dir, "config", layer_type[0],report_pack[0], validation)))
                        for table in all_configs:
                            yaml_paths.extend([(os.path.join(base_dir, "config", layer_type[0],report_pack[0], validation,f"{table}"))])
        
                    else:
                        for table in table_list:
                            yaml_paths.extend([(os.path.join(base_dir, "config", layer_type[0],report_pack[0], validation,f"{table}.yaml"))])
        
                    configpaths[validation] = yaml_paths
                    outputpaths[validation] = path
                    
                    os.makedirs(path, exist_ok=True)

    else:
        logpath = os.path.join(
            output_dir,
            layer_type[0],
            f"validation_{run_id}"
        )
        os.makedirs(output_dir, exist_ok=True)
        #Creating directories based on parameters passed
        for validation in validation_dirs:
            if validation == 'count_validation':
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

            if validation == 'data_validation':
                path = os.path.join(
                    output_dir,
                    layer_type[0],
                    f"validation_{run_id}",
                    f"{validation}_{run_id}" 
                )

                yaml_paths = []
                if 'all' in table_list:
                    all_configs = (os.listdir(os.path.join(base_dir, "config", layer_type[0], validation)))
                    for table in all_configs:
                        yaml_paths.extend([(os.path.join(base_dir, "config", layer_type[0], validation,f"{table}"))])

                else:
                    for table in table_list:
                        yaml_paths.extend([(os.path.join(base_dir, "config", layer_type[0], validation,f"{table}.yaml"))])

                configpaths[validation] = yaml_paths
                outputpaths[validation] = path
                
                os.makedirs(path, exist_ok=True)

    return outputpaths,configpaths,logpath

def create_summary(run_at,run_id,validation_type,source_table_name,source_type,target_table_name,target_type,status,output_path,source_rows=0,target_rows=0,output_file_path=None,batch_start_time=None,batch_end_time=None,diff_batch=None,missing_in_source=None,missing_in_target=None,error_message=None,layer_type=None,report_pack=None,report_tile=None,test_case=None,summary=None):
    if validation_type != 'count_validation':
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
                "status": status,
                "output_file_path":output_file_path,
                "batch_start_time": batch_start_time,
                "batch_end_time": batch_end_time,
                "total_time_taken": diff_batch,
                "error_message": error_message
            }

            summary_df = pd.DataFrame([summary_dict])
        else:
            summary_df = pd.DataFrame([summary_dict])

    else:

        summary_df = pd.DataFrame([{
            "run_id": run_id,
            "run_at": run_at,
            "validation_performed": validation_type,
            "source_table_name": source_table_name,
            "source_type": source_type,
            "target_table_name": target_table_name,
            "target_type": target_type,
            "source_count": source_rows ,
            "target_count": target_rows,
            "count_difference": abs(int(target_rows)-(int(source_rows))),
            "status": status,
            "batch_start_time": batch_start_time,
            "batch_end_time": batch_end_time,
            "total_time_taken": diff_batch,
            "error_message": error_message
        }])

    summary_file = os.path.join(output_path,f"{validation_type}_summary.csv")

    summary_df.to_csv(summary_file,
        mode="a",
        index=False,
        header=not os.path.exists(summary_file))

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