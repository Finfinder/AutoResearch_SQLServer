# db.py
import pyodbc

def get_connection():
    return pyodbc.connect(
        "DRIVER={ODBC Driver 17 for SQL Server};"
        "SERVER=localhost;"
        "DATABASE=TwojaBaza;"
        "UID=sa;"
        "PWD=TwojeHaslo;"
    )