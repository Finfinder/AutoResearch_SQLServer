# runner.py
import re
import time
from db import get_connection
from stats_parser import parse_io_stats, parse_time_stats, parse_execution_plan

_COMMENT_PATTERN = re.compile(r"--[^\n]*|/\*.*?\*/", re.DOTALL)


def _clear_cache(cursor):
    try:
        cursor.execute("DBCC DROPCLEANBUFFERS")
    except Exception as e:
        print(f"⚠️  Warning: DROPCLEANBUFFERS skipped (missing ALTER SERVER STATE permission): {e}")
    try:
        cursor.execute("DBCC FREEPROCCACHE")
    except Exception as e:
        print(f"⚠️  Warning: FREEPROCCACHE skipped (missing ALTER SERVER STATE permission): {e}")


def _collect_execution_plan(cursor, warnings):
    plan_xml = None
    execution_plan = {}
    try:
        while cursor.nextset():
            try:
                row = cursor.fetchone()
                if row and isinstance(row[0], str) and "<ShowPlanXML" in row[0]:
                    plan_xml = row[0]
                    execution_plan = parse_execution_plan(plan_xml)
                    break
            except Exception:
                continue
    except Exception as e:
        warnings.append(f"Execution plan retrieval failed: {e}")
    return plan_xml, execution_plan


def run_query(query):
    conn = get_connection()
    cursor = None
    try:
        cursor = conn.cursor()
        warnings = []

        _clear_cache(cursor)

        cursor.execute("SET STATISTICS IO ON")
        cursor.execute("SET STATISTICS TIME ON")

        xml_enabled = True
        try:
            cursor.execute("SET STATISTICS XML ON")
        except Exception as e:
            xml_enabled = False
            msg = f"Execution plan unavailable (missing SHOWPLAN permission): {e}"
            warnings.append(msg)
            print(f"⚠️  Warning: SET STATISTICS XML skipped: {e}")

        start = time.perf_counter()
        try:
            cursor.execute(query)
            cursor.fetchall()
            duration = time.perf_counter() - start
        except Exception as e:
            return {
                "time": None,
                "error": str(e),
                "server_metrics": {},
                "execution_plan": {},
                "plan_xml": None,
                "query_store": None,
                "warnings": warnings,
            }

        messages = list(cursor.messages)
        io_stats = parse_io_stats(messages)
        time_stats = parse_time_stats(messages)
        server_metrics = {**io_stats, **time_stats}

        plan_xml, execution_plan = None, {}
        if xml_enabled:
            plan_xml, execution_plan = _collect_execution_plan(cursor, warnings)

        query_store = _fetch_query_store(cursor, query, warnings)

        if not server_metrics and execution_plan:
            runtime = execution_plan.get("runtime_stats", {})
            if runtime:
                server_metrics = runtime

        return {
            "time": duration,
            "error": None,
            "server_metrics": server_metrics,
            "execution_plan": execution_plan,
            "plan_xml": plan_xml,
            "query_store": query_store,
            "warnings": warnings,
        }
    finally:
        try:
            if cursor:
                cursor.close()
        finally:
            conn.close()


def _fetch_query_store(cursor, query_text, warnings):
    try:
        try:
            cursor.execute("EXEC sp_query_store_flush_db")
        except Exception:
            pass

        stripped = _COMMENT_PATTERN.sub("", query_text).strip()[:150]
        escaped = stripped.replace("[", "[[]").replace("%", "[%]").replace("_", "[_]")
        pattern = re.sub(r"\s+", "%", escaped)

        qs_query = (
            "SELECT TOP 1 "
            "rs.avg_duration, rs.avg_cpu_time, "
            "rs.avg_logical_io_reads, rs.avg_physical_io_reads, "
            "rs.avg_query_max_used_memory "
            "FROM sys.query_store_runtime_stats rs "
            "JOIN sys.query_store_plan p ON rs.plan_id = p.plan_id "
            "JOIN sys.query_store_query q ON p.query_id = q.query_id "
            "JOIN sys.query_store_query_text qt ON q.query_text_id = qt.query_text_id "
            "WHERE qt.query_sql_text LIKE ? "
            "ORDER BY rs.last_execution_time DESC"
        )
        cursor.execute(qs_query, ("%" + pattern + "%",))
        row = cursor.fetchone()
        if row:
            return {
                "avg_duration_us": row[0],
                "avg_cpu_time_us": row[1],
                "avg_logical_io_reads": row[2],
                "avg_physical_io_reads": row[3],
                "avg_memory_grant_kb": int(row[4] * 8) if row[4] else None,
            }
        warnings.append("Query Store: no data found for this query variant")
        return None
    except Exception as e:
        warnings.append(f"Query Store unavailable: {e}")
        return None