# Data Validation Framework

## Overview

The Data Validation Framework performs automated validation between source and target database systems across different data layers.

The framework supports the following validation types:

- Count validation
- Data validation

Supported data layers:

- Bronze
- Silver
- Gold
- Reporting

Supported database systems:

- PostgreSQL
- Microsoft SQL Server
- Snowflake


The framework compares source and target query results and generates summary files and mismatch reports.

---

## Features

### Count Validation

Count validation compares the number of records available in the source and target systems.

Example query:

```sql
SELECT COUNT(*)
FROM customer;
```

Validation result:

- PASS when the source and target counts match
- FAIL when the source and target counts do not match

### Data Validation

Data validation compares the actual records returned by the source and target queries.

```sql
SELECT CorpID, CustomerName, Status FROM customer;
```

Validation result:

- PASS when the source and target datasets are identical
- FAIL when differences are detected

When a mismatch is found, the framework generates a CSV file containing the differences.

---

## Project Structure

```text
project/
|
|-- config/
|   |-- count_validation/
|   |-- data_validation/
|
|-- db/
|   |-- base.py
|   |-- factory.py
|   |-- postgres.py
|   |-- mssqlserver.py
|
|-- creds/
|    |--dev.yaml
|    |--uat.yaml
|    |--prod.yaml
|
|-- output/
|
|-- utils/
|   |-- utility.py
|
|-- main.py
|-- README.md
```

---

## Prerequisites

Make sure Python is installed and available from the command line.

The framework requires the following Python packages:

```text
pandas
PyYAML
pyodbc
psycopg2
```

Install the required packages using:

```bash
python -m pip install pandas PyYAML pyodbc psycopg2-binary
```

For Microsoft SQL Server connections, the appropriate Microsoft ODBC driver must also be installed on the machine.

---

## Command-Line Arguments

### layer_type

Specifies the data layer that must be validated.

Example:

```bash
--layer_type bronze
```

Allowed values:

```text
bronze
silver
gold
reporting
```

### tables

Specifies the tables that must be validated.

Validate one table:

```bash
--tables Customer
```

Validate multiple tables:

```bash
--tables Customer Product Sales
```

Validate all tables available in the configuration:

```bash
--tables all
```

### count_validation

Enables or disables count validation.

Example:

```bash
--count_validation yes
```

Allowed values:

```text
yes
no
```

### data_validation

Enables or disables data validation.

Example:

```bash
--data_validation yes
```

Allowed values:

```text
yes
no
```

---

## Execution Examples

The following examples use Windows Command Prompt syntax.

### Run Count Validation Only

```bat
python main.py 
  --layer_type bronze 
  --tables all 
  --count_validation yes 
  --data_validation no
```

The same command can be entered on a single line:

```bat
python main.py --layer_type bronze --tables all --count_validation yes --data_validation no
```

### Run Data Validation Only

```bat
python main.py 
  --layer_type bronze 
  --tables all 
  --count_validation no 
  --data_validation yes
```

### Run Both Validations

```bat
python main.py 
  --layer_type bronze 
  --tables all 
  --count_validation yes 
  --data_validation yes
```

### Validate Selected Tables

```bat
python main.py 
  --layer_type silver 
  --tables Customer Product 
  --count_validation yes 
  --data_validation yes
```

---

## Configuration File Structure

The validation configuration is stored in YAML files.

### Count Validation Configuration

```yaml
tables:
  Customer:
    validations:
      count_validation:
        source: postgres
        sourcequery: "SELECT COUNT(*) FROM customer"
        target: sqlserver
        targetquery: "SELECT COUNT(*) FROM customer"
        source_table_name: customer
        target_table_name: customer
```

### Data Validation Configuration

Specify the required columns explicitly in the source and target queries.

```yaml
tables:
  Customer:
    validations:
      data_validation:
        source: postgres
        sourcequery: "SELECT CorpID, CustomerName, Status FROM customer"
        target: sqlserver
        targetquery: "SELECT CorpID, CustomerName, Status FROM customer"
        sourcecolumn: CorpID
        targetcolumn: CorpID
        source_table_name: customer
        target_table_name: customer
```

Both queries should return compatible columns in the same order and with compatible data types.

---

## Validation Process

The framework performs the following steps:

1. Reads the command-line arguments.
2. Generates a unique run ID.
3. Identifies the required validation directories.
4. Loads the appropriate YAML configuration files.
5. Creates source and target database connections.
6. Executes the configured source and target queries.
7. Compares the query results.
8. Generates a validation summary.
9. Generates a mismatch report when required.
10. Returns the appropriate process exit code.

---

## Output Files

### Summary Report

A summary report is generated for each validation.

The summary can contain:

- Run date and time
- Run ID
- Validation type
- Source system
- Source table name
- Target system
- Target table name
- Source row count
- Target row count
- Validation status
- Mismatch file path

### Mismatch Report

A mismatch report is generated when data validation fails.

Example filename:

```text
Customer_data_validation_result_RUN_ID.csv
```

The mismatch report contains column-level differences between common source and target records.

The current implementation uses `CorpID` as the comparison index for data validation.

---

## Validation Status

### PASS

A validation receives PASS status when the source and target results are equal.

### FAIL

A validation receives FAIL status when the source and target results are different.

Validation failures are recorded in the summary output. They do not cause the program to return exit code 1.

---

## Logging

The framework uses centralized logging.

The logs can include:

- Validation start and completion
- Run ID
- Input parameters
- Validation type
- Configuration file path
- Table name
- Source and target queries
- Source and target record counts
- Validation result
- Mismatch output path
- Database errors
- Unexpected errors

Example log output:

```text
INFO - Validation job started
INFO - Processing validation type: count_validation
INFO - Processing table: Customer
INFO - Match/Mismatch: Match
WARNING - Validation failed for table=Customer validation=data_validation
ERROR - Database or network error
INFO - Validation job completed
```

When `exc_info=True` is used with the logger, the complete exception traceback is included in the log.

---

## Exit Codes

### Exit Code 0

The program returns exit code 0 when no database or network error occurs.

This includes the following situations:

- All validations pass
- A count mismatch is found
- A data mismatch is found

Validation mismatches are treated as validation results rather than application failures.

### Exit Code 1

The program returns exit code 1 when a database or network error is detected.

Examples include:

- Database server unavailable
- Network connectivity failure
- Invalid database credentials
- Connection timeout
- PostgreSQL connection failure
- SQL Server connection failure

The final exit logic is:

```python
sys.exit(1 if system_error else 0)
```

---

## Error Handling

### Database and Network Errors

Database-related exceptions are handled separately:

```python
except (pyodbc.Error, psycopg2.Error):
    logger.error(
        "Database/network error for table=%s validation=%s",
        table_name,
        validation_name,
        exc_info=True
    )
    system_error = True
    continue
```

These errors set `system_error` to `True` and cause the program to return exit code 1 after processing is complete.

### Unexpected Errors

Other exceptions are logged separately:

```python
except Exception:
    logger.error(
        "Unexpected error for table=%s validation=%s",
        table_name,
        validation_name,
        exc_info=True
    )
    continue
```

The current implementation logs unexpected errors but does not set `system_error` to `True`.

---

## Supported Databases

### PostgreSQL

PostgreSQL connectivity is implemented using:

```python
psycopg2
```

### Microsoft SQL Server

Microsoft SQL Server connectivity is implemented using:

```python
pyodbc
```

---

## Validation Workflow

```text
Start
  |
  v
Read command-line arguments
  |
  v
Generate run ID
  |
  v
Load validation configuration
  |
  v
Connect to source database
  |
  v
Execute source query
  |
  v
Connect to target database
  |
  v
Execute target query
  |
  v
Compare source and target results
  |
  |-- Results match
  |      |
  |      v
  |    Set status to PASS
  |
  |-- Results do not match
         |
         v
       Set status to FAIL
         |
         v
       Generate mismatch report
  |
  v
Create validation summary
  |
  v
Check for system errors
  |
  |-- System error detected: exit code 1
  |
  |-- No system error detected: exit code 0
  |
  v
End
```

---

## Important Notes

- The source and target queries should return matching column names and compatible data types.
- Data validation currently uses `CorpID` as the record comparison key.
- The required output directories must be available or created by the utility functions.
- Database credentials should not be committed directly to source control.
- Sensitive configuration files should be excluded using `.gitignore`.
- The final `sys.exit` statement must remain outside all validation loops.

---

## Author

Arunsaikiran S

## Version

1.0