# db.py
import logging
import os

import pyodbc
from dotenv import load_dotenv

load_dotenv(override=False)

logger = logging.getLogger(__name__)


def get_connection():
    driver = os.environ.get("DB_DRIVER", "ODBC Driver 17 for SQL Server")
    server = os.environ.get("DB_SERVER")
    database = os.environ.get("DB_DATABASE")
    uid = os.environ.get("DB_UID")
    pwd = os.environ.get("DB_PWD")

    missing = [
        name
        for name, val in [
            ("DB_SERVER", server),
            ("DB_DATABASE", database),
            ("DB_UID", uid),
            ("DB_PWD", pwd),
        ]
        if not val
    ]
    if missing:
        raise ValueError(
            f"Missing required environment variables: {', '.join(missing)}. "
            "Copy .env.example to .env and fill in the values."
        )

    logger.debug("Connecting to %s/%s as %s", server, database, uid)
    return pyodbc.connect(
        f"DRIVER={{{driver}}};"
        f"SERVER={server};"
        f"DATABASE={database};"
        f"UID={uid};"
        f"PWD={pwd};"
        "TrustServerCertificate=yes;"
    )