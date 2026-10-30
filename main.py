import os
import sys
import time
from datetime import datetime
# CHANGE: YAML generation. replace() builds the schema validation context with the tables as requested (see schema_ctx below).
from dataclasses import replace
from db.factory import close_all_databases
from utils.utility import generate_runid, get_config_output_paths, get_logger, add_file_handler, parse_arguments, data_context
from validators.schema_validation import validate_schema
from validators.integrity_check import validate_integrity
from validators.count_validation import validate_count
from validators.data_validation import validate_data

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

    # CHANGE: YAML generation. With --yaml_generation yes the table list is only known once the YAML is written, so the paths
    # are worked out again after that (below). An empty list here stops an 'all' listing of a folder that may not exist yet.
    yaml_generation = args.yaml_generation[0] == 'yes'
    outputpaths,configpaths,logpath = get_config_output_paths(run_id,layer,report_pack,BASE_DIR,config_path,validation_dirs,[] if yaml_generation else tables)


    if logpath is None:
        raise ValueError(f"Unsupported layer_type '{layer[0]}': no log path could be determined")
    # CHANGE: YAML generation. The log folder is made here for a YAML run, so it exists even when no validator runs (both validation
    # flags 'no'). Without this the file handler below fails. Runs with the flag off are unchanged.
    if yaml_generation:
        os.makedirs(logpath, exist_ok=True)
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

    # CHANGE: YAML generation. The tables as requested, kept before generation replaces them with the ready list.
    requested_tables = tables
    # CHANGE: YAML generation. With --yaml_generation yes the YAML for the requested tables is checked against the live source and
    # Snowflake structure and rewritten where it differs, before any validator runs. Only the tables that are ready are validated.
    # A table that FAILED is left out (no table is skipped: a table with no primary key FAILS). The run exits 1 when a table FAILED or no table is ready.
    if yaml_generation:
        # CHANGE: YAML generation. Imported here, not at the top of the file, so a run with the flag off does not need the yaml_generation folder.
        from yaml_generation.generate_yaml import run_for_validation
        # CHANGE: YAML generation. With both validation flags 'no', the YAML is still written for both kinds and no validator runs.
        generate_only = args.data_validation[0] == 'no' and args.count_validation[0] == 'no'
        try:
            generation_results, tables = run_for_validation(
                layer_type=layer[0],
                tables=tables,
                environment=environment,
                run_type=run_type,
                data_validation='yes' if generate_only else args.data_validation[0],
                count_validation='yes' if generate_only else args.count_validation[0],
                source_schema=args.source_schema,
                target_database=args.target_database,
                target_schema=args.target_schema,
                # CHANGE: YAML generation. --filter_files 'yes' applies the row filters from yaml_generation/Filter-files-excel/.
                use_filter_files=args.filter_files[0] == 'yes',
                log=logger,
            )
        except Exception:
            # CHANGE: YAML generation. Any error, not only ValueError, ends the run here: nothing is validated from an old copy of the YAML.
            logger.error("YAML generation did not run, so nothing was validated", exc_info=True)
            close_all_databases()
            sys.exit(1)

        not_validated = sorted({f"{table} ({kind}: {status})"
                                for table, kind, status, _ in generation_results
                                if status not in ("NEW", "UPDATED", "UNCHANGED", "PRESENT")})
        if not_validated:
            logger.warning("Not validated by this run: %s", "; ".join(not_validated))
        if any(status == "FAILED" for _, _, status, _ in generation_results):
            system_error = True
        if not tables:
            logger.error("YAML generation: no table is ready to validate. See the YAML lines above.")
            close_all_databases()
            sys.exit(1)

        outputpaths,configpaths,logpath = get_config_output_paths(run_id,layer,report_pack,BASE_DIR,config_path,validation_dirs,tables)
        # CHANGE: YAML generation. Only the data YAML of a table that is ready for data is validated. A table that FAILED for data (for
        # example no primary key) gets none, and an older hand-written file for it must not run in its place.
        data_ready = {table for table, kind, status, _ in generation_results
                      if kind == "data_validation" and status in ("NEW", "UPDATED", "UNCHANGED", "PRESENT")}
        if "data_validation" in configpaths:
            configpaths["data_validation"] = [path for path in configpaths["data_validation"]
                                              if os.path.splitext(os.path.basename(path))[0] in data_ready]

    # CHANGE: YAML generation. Without --yaml_generation, a named table whose YAML is missing is created now. A YAML that is
    # already there is used as it is. 'all' is not checked here, because its list comes from the source.
    if not yaml_generation and "all" not in tables and layer[0] in ("bronze_postgres", "bronze_mssql"):
        from yaml_generation.generate_yaml import run_for_validation, tables_missing_yaml
        missing = tables_missing_yaml(layer[0], run_type, tables, args.data_validation[0], args.count_validation[0])
        if missing:
            # CHANGE: YAML generation. A created incremental YAML needs no --filter_files now (it has the window).
            logger.info("YAML missing for %s: creating it", ", ".join(missing))
            try:
                creation_results, _ = run_for_validation(
                    layer_type=layer[0],
                    tables=missing,
                    environment=environment,
                    run_type=run_type,
                    data_validation=args.data_validation[0],
                    count_validation=args.count_validation[0],
                    source_schema=args.source_schema,
                    target_database=args.target_database,
                    target_schema=args.target_schema,
                    use_filter_files=args.filter_files[0] == 'yes',
                    create_only=True,
                    log=logger,
                )
            except Exception:
                logger.error("YAML creation did not run, so nothing was validated", exc_info=True)
                close_all_databases()
                sys.exit(1)
            failed = sorted({table for table, _, status, _ in creation_results if status == "FAILED"})
            if failed:
                logger.error("YAML could not be created for %s. These tables are not validated.", ", ".join(failed))
                system_error = True
            tables = [table for table in tables if table not in failed]
            if not tables:
                logger.error("YAML creation: no table is left to validate. See the YAML lines above.")
                close_all_databases()
                sys.exit(1)
            outputpaths,configpaths,logpath = get_config_output_paths(run_id,layer,report_pack,BASE_DIR,config_path,validation_dirs,tables)
            # A data YAML that is still not there (for example a table with no primary key) is not validated.
            if "data_validation" in configpaths:
                not_there = [os.path.splitext(os.path.basename(path))[0] for path in configpaths["data_validation"]
                             if not os.path.exists(path)]
                if not_there:
                    logger.warning("Data YAML not there, so not validated: %s", ", ".join(not_there))
                configpaths["data_validation"] = [path for path in configpaths["data_validation"] if os.path.exists(path)]

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
            )
    # CHANGE: YAML generation. The validators no longer skip the Fivetran freshness check for a generated YAML: it is
    # removed from the context, because no validator reads the flag any more.
    # CHANGE: YAML generation. Schema validation reads no generated YAML, so it gets the tables as requested (for example 'all').
    # The count and data validators get the ready list, which is what the generated YAML covers.
    schema_ctx = replace(ctx, tables=requested_tables)

    validators_to_run = []
    module_dict = {'validate_integrity':'Integrity validation','validate_schema':'Schema validation','validate_count':'Count validation','validate_data':'Data validation'}
    if layer[0] == "sanity" and "integrity_check" in outputpaths:
        validators_to_run.append(validate_integrity)
    for validation in validation_dirs:
        if validation == "schema_validation":
            validators_to_run.append(validate_schema)
        elif validation == "count_validation":
            validators_to_run.append(validate_count)
        elif validation == "data_validation":
            validators_to_run.append(validate_data)
    m_d = {}
    for run_validator in validators_to_run:
        try:
            # CHANGE: YAML generation. Schema validation takes schema_ctx (the tables as requested), the others take ctx.
            failures, sys_err = run_validator(schema_ctx if run_validator is validate_schema else ctx)
            m_d[module_dict[run_validator.__name__]] = failures
            failure_count += failures
            system_error = system_error or sys_err
        except Exception:
            logger.error("Validator %s crashed", run_validator.__module__, exc_info=True)
            system_error = True
    end_time = datetime.now()
    total_time_taken = time.strftime("%H:%M:%S", time.gmtime((end_time - start_time).total_seconds()))
    logger.info("Validation job completed")
    logger.info("Duration: %s", total_time_taken)
    for validation,failures in m_d.items():
        logger.info(f"{validation} - Failure count: {failures}")

    logger.info("Total failures: %s", failure_count)
    logger.info("Run ID: %s", run_id)
    close_all_databases()
    if system_error:
        exit_code = 1
    elif failure_count > 0:
        exit_code = 2
        logger.info("Failure counts is greater than zero")
    else:
        exit_code = 0
    #log failure reason for 2 
    logger.info("Exit code: %s", exit_code)
    sys.exit(exit_code)

if __name__ == "__main__":
    main()