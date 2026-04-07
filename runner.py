# runner.py
import time
from db import get_connection

def run_query(query):
    conn = get_connection()
    cursor = conn.cursor()

    try:
        cursor.execute("DBCC DROPCLEANBUFFERS")
    except Exception as e:
        print(f"⚠️  Warning: DROPCLEANBUFFERS skipped (missing ALTER SERVER STATE permission): {e}")

    try:
        cursor.execute("DBCC FREEPROCCACHE")
    except Exception as e:
        print(f"⚠️  Warning: FREEPROCCACHE skipped (missing ALTER SERVER STATE permission): {e}")

    start = time.time()

    try:
        cursor.execute(query)
        cursor.fetchall()
        duration = time.time() - start
    except Exception as e:
        return None, str(e)

    return duration, None