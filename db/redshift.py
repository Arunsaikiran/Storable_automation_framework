import redshift_connector
import pandas as pd
from db.base import Database

class Redshift(Database):
        def __init__(self,REDSHIFT_HOST,REDSHIFT_PORT,REDSHIFT_DATABASE,REDSHIFT_USER,REDSHIFT_PASSWORD):
                self.REDSHIFT_HOST = REDSHIFT_HOST
                self.REDSHIFT_PORT = REDSHIFT_PORT
                self.REDSHIFT_DATABASE = REDSHIFT_DATABASE
                self.REDSHIFT_USER = REDSHIFT_USER
                self.REDSHIFT_PASSWORD = REDSHIFT_PASSWORD
                self._conn = None
        def connect(self):
                if self._conn is None:
                        self._conn = redshift_connector.connect(
                                host=self.REDSHIFT_HOST,
                                port=self.REDSHIFT_PORT,
                                database=self.REDSHIFT_DATABASE,
                                user=self.REDSHIFT_USER,
                                password=self.REDSHIFT_PASSWORD,
                            )
                return self._conn
        def execute_query(self, query):
                conn = self.connect()
                try:
                    with conn.cursor() as cur:
                        cur.execute(query)
                        col_names = [desc[0] for desc in cur.description]
                        rows = cur.fetchall()
                        return pd.DataFrame(rows, columns=col_names)
                except Exception:
                    conn.rollback()
                    raise
        def close(self):
                if self._conn is not None:
                        self._conn.close()
                self._conn = None