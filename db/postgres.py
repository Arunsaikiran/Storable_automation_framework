from db.base import Database
import psycopg2
import pandas as pd


class Postgres(Database):
    def __init__(self,dbname,user,password,host,port):
        self.dbname = dbname
        self.user = user
        self.password = password
        self.host = host
        self.port = port
        self._conn = None

    def connect(self):
        if self._conn is None or self._conn.closed:
            self._conn = psycopg2.connect(
                dbname=self.dbname,
                user=self.user,
                password=self.password,
                host=self.host,
                port=self.port
            )
        return self._conn

    def execute_query(self, query):

        conn = self.connect()
        cur = None
        try:
            cur = conn.cursor()
            cur.execute(query)
            data = cur.fetchall()
            columns = [desc[0] for desc in cur.description] # type: ignore
            return pd.DataFrame(data, columns=columns)
        except Exception:
            conn.rollback()
            raise
        finally:
            if cur is not None:
                cur.close()

    def close(self):
        if self._conn is not None and not self._conn.closed:
            self._conn.close()
        self._conn = None