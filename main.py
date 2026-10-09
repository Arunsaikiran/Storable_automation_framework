import os
import sys
import time
from datetime import datetime
from db.factory import close_all_databases
from utils.utility import generate_runid, get_config_output_paths, get_logger, add_file_handler, parse_arguments, data_context
from validators.schema_validation import validate_schema
from validators.integrity_check import validate as validate_integrity
from validators.count_validation import validate as validate_count
from validators.data_validation import validate as validate_data

def main():
    start_time = datetime.now()
    run_id,run_at = generate_runid()
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))
    base_config_path = os.path.join(BASE_DIR,"config")

    failure_count = 0
    system_error = False

    logger = get_logger(__name__)


    args = parse_arguments()

    layer = args.layer_type
    report_pack = args.report_pack
    tables = args.tables
    environment = args.environment[0]
    from_date = args.from_date
    to_date = args.to_date
    run_type = args.run_type

    if run_type == "historical":
        config_path = os.path.join(base_config_path,"historical")
    else:
        config_path = os.path.join(base_config_path,"incremental")


    validation_dirs = []
    if args.schema_validation[0] == 'yes':
        validation_dirs.append("schema_validation")
    if args.count_validation[0] == 'yes':
        validation_dirs.append("count_validation")
    if args.data_validation[0] == 'yes':
        validation_dirs.append("data_validation")
    if args.integrity_check[0] == 'yes':
        validation_dirs.append("integrity_check")

    outputpaths,configpaths,logpath = get_config_output_paths(run_id,layer,report_pack,BASE_DIR,config_path,validation_dirs,tables)


    if logpath is None:
        raise ValueError(f"Unsupported layer_type '{layer[0]}': no log path could be determined")
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

    DataContext = data_context()
    ctx = DataContext(
            run_id=run_id,
            run_at=run_at,
            validation_dirs = validation_dirs,
            layer_type = layer,
            outputpaths=outputpaths,
            configpaths=configpaths,
            BASE_DIR=BASE_DIR,
            environment=environment,
            tables=tables,
            run_type=run_type,
            report_pack=report_pack,
            from_date = from_date,
            to_date = to_date
            )

    validators_to_run = []
    if layer[0] == "sanity" and "integrity_check" in outputpaths:
        validators_to_run.append(validate_integrity)
    for validation in validation_dirs:
        if validation == "schema_validation":
            validators_to_run.append(validate_schema)
        elif validation == "count_validation":
            validators_to_run.append(validate_count)
        elif validation == "data_validation":
            validators_to_run.append(validate_data)

    for run_validator in validators_to_run:
        try:
            failures, sys_err = run_validator(ctx)
            failure_count += failures
            system_error = system_error or sys_err
        except Exception:
            logger.error("Validator %s crashed", run_validator.__module__, exc_info=True)
            system_error = True

    end_time = datetime.now()
    total_time_taken = time.strftime("%H:%M:%S", time.gmtime((end_time - start_time).total_seconds()))
    logger.info("Validation job completed")
    logger.info("Duration: %s", total_time_taken)
    logger.info("Total failures: %s", failure_count)
    logger.info("Run ID: %s", run_id)
    close_all_databases()
    if system_error:
        exit_code = 1
    elif failure_count > 0:
        exit_code = 2
    else:
        exit_code = 0
    logger.info("Exit code: %s", exit_code)
    sys.exit(exit_code)

if __name__ == "__main__":
    main()