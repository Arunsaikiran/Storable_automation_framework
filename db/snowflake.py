from db.base import Database
import snowflake.connector
import pandas as pd


class Snowflake(Database):
    def __init__(self,SNOWFLAKE_ACCOUNT,SNOWFLAKE_USER,SNOWFLAKE_ROLE,externalbrowser,SNOWFLAKE_DATABASE,SNOWFLAKE_SCHEMA,SNOWFLAKE_WAREHOUSE):
        self.SNOWFLAKE_ACCOUNT = SNOWFLAKE_ACCOUNT
        self.SNOWFLAKE_USER = SNOWFLAKE_USER
        self.SNOWFLAKE_ROLE = SNOWFLAKE_ROLE
        self.externalbrowser = externalbrowser
        self.SNOWFLAKE_DATABASE = SNOWFLAKE_DATABASE
        self.SNOWFLAKE_SCHEMA = SNOWFLAKE_SCHEMA
        self.SNOWFLAKE_WAREHOUSE = SNOWFLAKE_WAREHOUSE

    def connect(self):
        
        conn = snowflake.connector.connect(
            account=self.SNOWFLAKE_ACCOUNT,
            user=self.SNOWFLAKE_USER,
            role=self.SNOWFLAKE_ROLE,
            authenticator=self.externalbrowser,
            database=self.SNOWFLAKE_DATABASE,
            schema=self.SNOWFLAKE_SCHEMA,
            warehouse=self.SNOWFLAKE_WAREHOUSE 
        )
        return conn

    def execute_query(self, query):
        with self.connect() as conn:
            with conn.cursor() as cs:
                cs.execute("USE WAREHOUSE DEVELOPER_WH;")
                cs.execute(query)
                rows = cs.fetchall()
                df = pd.DataFrame(rows,columns=[c[0] for c in cs.description])
                return df
 

        # with conn.cursor() as cur:
        #     cur.execute(query)
        #     data = cur.fetchall()

        #     columns = [desc[0] for desc in cur.description]

        #     return pd.DataFrame(data, columns=columns)