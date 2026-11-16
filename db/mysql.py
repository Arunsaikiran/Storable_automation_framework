# CHANGE: YAML generation. New file: the MySQL connector for --layer_type bronze_mysql_crm (the creds block 'mysql_crm').
# It follows db/mssqlserver.py: one connection, reused, and execute_query returns a pandas DataFrame.
from db.base import Database
import pandas as pd
import pymysql


class Mysql(Database):
    def __init__(self, host, port, database, user, password):
        self.host = host
        self.port = int(port)
        self.database = database
        self.user = user
        self.password = password
        self._conn = None

    def connect(self):
        if self._conn is None or not self._conn.open:
            self._conn = pymysql.connect(
                host=self.host,
                port=self.port,
                database=self.database,
                user=self.user,
                password=self.password,
                charset="utf8mb4",
            )
        return self._conn

    def execute_query(self, query):
        conn = self.connect()
        cur = conn.cursor()
        try:
            cur.execute(query)
            columns = [desc[0] for desc in cur.description]
            return pd.DataFrame(cur.fetchall(), columns=columns)
        except Exception:
            conn.rollback()
            raise
        finally:
            cur.close()

    def close(self):
        if self._conn is not None and self._conn.open:
            self._conn.close()
        self._conn = None
