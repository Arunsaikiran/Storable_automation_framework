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
        self._conn = None

    def connect(self):
        if self._conn is None or self._conn.is_closed():
            self._conn = snowflake.connector.connect(
                account=self.SNOWFLAKE_ACCOUNT,
                user=self.SNOWFLAKE_USER,
                role=self.SNOWFLAKE_ROLE,
                authenticator=self.externalbrowser,
                database=self.SNOWFLAKE_DATABASE,
                schema=self.SNOWFLAKE_SCHEMA,
                warehouse=self.SNOWFLAKE_WAREHOUSE
            )
        return self._conn

    def execute_query(self, query):
        conn = self.connect()
        with conn.cursor() as cs:
            cs.execute(query)
            rows = cs.fetchall()
            return pd.DataFrame(rows,columns=[c[0] for c in cs.description])

    def close(self):
        if self._conn is not None and not self._conn.is_closed():
            self._conn.close()
        self._conn = None
