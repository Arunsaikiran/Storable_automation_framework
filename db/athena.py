from db.base import Database
import awswrangler as wr
import boto3
import time
import pandas as pd


class Athena(Database):
    def __init__(self, PROFILE, AWS_REGION, ATHENA_DB, ATHENA_OUTPUT):
        self.PROFILE = PROFILE
        self.AWS_REGION = AWS_REGION
        self.ATHENA_DB = ATHENA_DB
        self.ATHENA_OUTPUT = ATHENA_OUTPUT

    def connect(self):
        session = boto3.Session(profile_name=self.PROFILE, region_name=self.AWS_REGION)
        return session

    def execute_query(self, query):
        session = self.connect()
        athena = session.client("athena")

        response = athena.start_query_execution(
            QueryString=query,
            QueryExecutionContext={"Database": self.ATHENA_DB},
            ResultConfiguration={"OutputLocation": self.ATHENA_OUTPUT},
        )

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

        # Get the EXACT result file path (not a folder/prefix)
        s3_path = status["QueryExecution"]["ResultConfiguration"]["OutputLocation"]
        print(f"Reading result from: {s3_path}")  # sanity check — should end in .csv

        try:
            df = wr.s3.read_csv(s3_path, boto3_session=session)
        except UnicodeDecodeError:
            # Fallback: fetch raw bytes ourselves and decode leniently
            import io
            s3 = session.client("s3")
            bucket, key = s3_path.replace("s3://", "").split("/", 1)
            obj = s3.get_object(Bucket=bucket, Key=key)
            raw_bytes = obj["Body"].read()

            # Try a few common encodings before giving up
            for enc in ["utf-8-sig", "latin-1", "cp1252"]:
                try:
                    text = raw_bytes.decode(enc)
                    df = pd.read_csv(io.StringIO(text))
                    break
                except UnicodeDecodeError:
                    continue
            else:
                raise

        return df