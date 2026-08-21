from db.base import Database
import pandas as pd
import boto3
import time


class Athena(Database):
    def __init__(self,PROFILE,AWS_REGION,ATHENA_DB,ATHENA_OUTPUT):
        self.PROFILE = PROFILE
        self.AWS_REGION = AWS_REGION
        self.ATHENA_DB = ATHENA_DB
        self.ATHENA_OUTPUT = ATHENA_OUTPUT

    def connect(self):
        session = boto3.Session(profile_name=self.PROFILE, region_name=self.AWS_REGION)
        athena = session.client("athena")
        return athena
    
    def execute_query(self, query):
        athena = self.connect()
        response = athena.start_query_execution(
        QueryString=query,
        QueryExecutionContext={"Database": self.ATHENA_DB},
        ResultConfiguration={"OutputLocation": self.ATHENA_OUTPUT},)

        query_execution_id = response["QueryExecutionId"]

        # Wait for completion
        while True:
            status = athena.get_query_execution(QueryExecutionId=query_execution_id)
            state = status["QueryExecution"]["Status"]["State"]
            if state in ["SUCCEEDED", "FAILED", "CANCELLED"]:
                break
            time.sleep(2)

        if state != "SUCCEEDED":
            reason = status["QueryExecution"]["Status"].get("StateChangeReason", "No details")
            raise Exception(f"Query failed: {state} — {reason}")


        # Fetch result
        results = athena.get_query_results(QueryExecutionId=query_execution_id)
        column_info = results["ResultSet"]["ResultSetMetadata"]["ColumnInfo"]
        col_names = [col["Name"] for col in column_info]

        rows = []
        for row in results["ResultSet"]["Rows"][1:]:  # skip header
            values = [c.get("VarCharValue", None) for c in row["Data"]]
            rows.append(values)

        return pd.DataFrame(rows, columns=col_names)