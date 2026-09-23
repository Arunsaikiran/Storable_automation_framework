from db.base import Database
import pandas as pd
import pyodbc

class Mssqlserver(Database):
    def __init__(self,DRIVER,SERVER,DATABASE,UID,PWD):
        self.DRIVER = DRIVER
        self.SERVER = SERVER
        self.DATABASE = DATABASE
        self.UID = UID
        self.PWD = PWD
        self._conn = None

    def connect(self):
        if self._conn is not None:
            return self._conn

        if self.UID and self.PWD:
            self._conn = pyodbc.connect(
                    f"DRIVER={{{self.DRIVER}}};"
                    f"SERVER=tcp:{self.SERVER},1400;"#,1400 add for storable mssqlserver
                    f"DATABASE={self.DATABASE};"
                    f"UID={self.UID};"
                    f"PWD={self.PWD};"
                    "TrustServerCertificate=yes;"
                )
        else:
            self._conn = pyodbc.connect(
                        f"DRIVER={{{self.DRIVER}}};"
                        f"SERVER={self.SERVER};"
                        f"DATABASE={self.DATABASE};"
                        "Trusted_Connection=yes;"
                        "Encrypt=yes;"
                        "TrustServerCertificate=yes;"
                        )
        return self._conn

    def execute_query(self,query):

        conn = self.connect()
        try:
            return pd.read_sql(query, conn)
        except Exception:
            conn.rollback()
            raise

    def close(self):
        if self._conn is not None:
            self._conn.close()
        self._conn = None 