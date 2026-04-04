# runner.py
import time
from db import get_connection

def run_query(query):
    conn = get_connection()
    cursor = conn.cursor()

    start = time.time()

    try:
        cursor.execute(query)
        cursor.fetchall()
        duration = time.time() - start
    except Exception as e:
        return None, str(e)

    return duration, None