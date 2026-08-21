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

    def connect(self):
        if self.UID and self.PWD: 
            conn = pyodbc.connect(
                    f"DRIVER={{{self.DRIVER}}};"
                    f"SERVER=tcp:{self.SERVER},1400;"#,1400 add for storable mssqlserver
                    f"DATABASE={self.DATABASE};"
                    f"UID={self.UID};"
                    f"PWD={self.PWD};"
                    "TrustServerCertificate=yes;"
                )
        else:
            conn = pyodbc.connect(
                        f"DRIVER={{{self.DRIVER}}};"
                        f"SERVER={self.SERVER};"
                        f"DATABASE={self.DATABASE};"
                        "Trusted_Connection=yes;"
                        "Encrypt=yes;"
                        "TrustServerCertificate=yes;"
                        )
            return conn

    def execute_query(self,query):
        
        conn = self.connect()

        try:
            df = pd.read_sql(
                query,
                conn
            )
        finally:
            assert conn is not None
            conn.close()

        return df 