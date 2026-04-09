# AutoResearch SQL Server

[![Python 3.10+](https://img.shields.io/badge/Python-3.10+-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![SQL Server](https://img.shields.io/badge/SQL%20Server-ODBC%2017-CC2927?logo=microsoftsqlserver&logoColor=white)](https://learn.microsoft.com/en-us/sql/connect/odbc/microsoft-odbc-driver-for-sql-server)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

Automated SQL query performance researcher for Microsoft SQL Server. Takes a base SQL query, generates structural variants (JOIN→EXISTS, NOLOCK, RECOMPILE), benchmarks each against a live database, and reports the fastest one. Additionally collects server-side metrics: IO (logical/physical reads), CPU time, memory grants and actual execution plans via SQL Server's built-in diagnostics — enabling multi-criteria ranking beyond wall-clock time alone.

---

## Use Cases

- **Query optimization** — quickly compare execution times of structurally different queries that produce the same results.
- **Hint testing** — evaluate the impact of query hints (`NOLOCK`, `OPTION (RECOMPILE)`) on real data.
- **Refactoring validation** — verify that a rewritten query is faster than the original before deploying to production.

---

## Requirements

- Python 3.10+
- Microsoft SQL Server (local or remote)
- [ODBC Driver 17 for SQL Server](https://learn.microsoft.com/en-us/sql/connect/odbc/download-odbc-driver-for-sql-server)
- Python packages: `pyodbc`, `python-dotenv`, `sqlglot`

> **Note**: Before each benchmark run, the tool executes `DBCC DROPCLEANBUFFERS` and `DBCC FREEPROCCACHE` to ensure cold-cache conditions. This requires the `ALTER SERVER STATE` permission (or `sysadmin` role).
>
> All diagnostic features use **graceful degradation** — if a permission is missing, a warning is printed and the benchmark continues with reduced metrics:
>
> | Feature | Required permission | Degradation behaviour |
> |---|---|---|
> | Cache clearing | `ALTER SERVER STATE` | Skipped — benchmark runs on warm cache |
> | Execution plan (`.sqlplan`) | `SHOWPLAN` | Plan not captured — IO/CPU still collected |
> | Query Store metrics | `VIEW DATABASE STATE` + Query Store enabled | Skipped — `query_store: null` in results |

---

## Installation

```bash
# 1. Clone the repository
git clone <repository-url>
cd AutoResearch_SQLServer

# 2. Create and activate a virtual environment
python -m venv .venv
.venv\Scripts\activate       # Windows
# source .venv/bin/activate  # Linux/macOS

# 3. Install dependencies
pip install -r requirements.txt
```

---

## Configuration

Credentials are read from environment variables, never hardcoded. For local development, copy `.env.example` to `.env` and fill in your values:

```bash
cp .env.example .env   # Linux/macOS
copy .env.example .env # Windows
```

Edit the resulting `.env` file:

```ini
# SQL Server host (e.g. localhost, server\instance)
DB_SERVER=localhost

# Target database
DB_DATABASE=your_database_name

# SQL Authentication credentials
DB_UID=your_username
DB_PWD=your_password

# Optional — defaults to "ODBC Driver 17 for SQL Server"
# DB_DRIVER=ODBC Driver 17 for SQL Server
```

| Variable | Required | Default | Description |
|---|---|---|---|
| `DB_SERVER` | ✅ | — | Hostname or IP of the SQL Server instance |
| `DB_DATABASE` | ✅ | — | Name of the target database |
| `DB_UID` | ✅ | — | SQL Server login (SQL Authentication) |
| `DB_PWD` | ✅ | — | SQL Server password |
| `DB_DRIVER` | ❌ | `ODBC Driver 17 for SQL Server` | ODBC driver name |
| `LOG_LEVEL` | ❌ | `INFO` | Console (stderr) log level. Accepted values: `DEBUG`, `INFO`, `WARNING`, `ERROR` |

> **Production / Docker**: set the variables directly in the environment (container runtime, secrets manager, CI/CD). Values from the environment always take priority over `.env`.

> **Security**: `.env` is listed in `.gitignore` and must never be committed to the repository.

---

## Logging

The tool uses Python's stdlib `logging` module with two output channels:

| Channel | Content | Format |
|---|---|---|
| **stdout** | Benchmark results (times, IO, CPU, memory grant, ranking) | Human-readable with emoji |
| **stderr** | Diagnostic messages (warnings, errors, info) | `TIMESTAMP LEVEL logger: message` |
| **`logs/autoresearch_YYYYMMDD_HHMMSS.log`** | Full history — diagnostics AND benchmark results | `TIMESTAMP LEVEL logger: message` |

Each run creates a new timestamped log file in the `logs/` directory (e.g. `logs/autoresearch_20260409_143022.log`). Log files are covered by `*.log` in `.gitignore`.

Control the verbosity of stderr output via the `LOG_LEVEL` environment variable (or `.env`):

```bash
# Suppress INFO messages (e.g. "Base row count") on stderr
LOG_LEVEL=WARNING python main.py

# Show all debug details including connection info (no password logged)
LOG_LEVEL=DEBUG python main.py

# Override inline without editing .env
LOG_LEVEL=DEBUG python main.py
```

Invalid values (e.g. `LOG_LEVEL=VERBOSE`) silently fall back to `INFO`.

Place your base SQL query in `query.sql`:

```sql
SELECT o.*
FROM [Sales].[SalesOrderHeader] o
JOIN [Sales].[Customer] c ON o.[CustomerID] = c.[CustomerID]
WHERE o.[OrderDate] > '2024-01-01'
```

---

## Quick Start

```bash
# Single run per variant (default)
python main.py

# Multiple runs per variant — aggregates mean ± stdev / median
python main.py --runs 5
```

The tool will:
1. Load the base query from `query.sql`
2. Generate structural variants via `variants.py`
3. Validate each variant: static guardrails (AST) then runtime row count check — blocked variants are skipped
4. Execute each valid variant with `SET STATISTICS IO ON`, `SET STATISTICS TIME ON`, and `SET STATISTICS XML ON`
5. Display per-variant server-side metrics (IO, CPU, memory grant)
6. Save all results (including validation info) to `results.json`
7. Save actual execution plans as `.sqlplan` files to `plans/` (openable in SSMS)
8. Print a multi-criteria ranking (time, IO, CPU, memory grant)

### CLI Arguments

| Argument | Default | Description |
|---|---|---|
| `--runs N` | `1` | Number of benchmark runs per variant. Clamped to [1, 100]. Cold cache cleared before each run. |


Example output:

```
Test 1/7 [JOIN→EXISTS]
⏱️  Time: 0.0187s (server: 10ms CPU / 18ms elapsed)
📊 IO: 45 logical reads, 0 physical reads
💾 Memory grant: 256 KB
Test 2/7 [NOLOCK]
⏱️  Time: 0.0245s (server: 12ms CPU / 24ms elapsed)
📊 IO: 60 logical reads, 0 physical reads
💾 Memory grant: 256 KB
⚠️  SpillToTempDb detected!
...

🏆 RANKING:
  ⏱️  Best by time:         [JOIN→EXISTS] — 0.0187s
  📊 Best by IO:            [JOIN→EXISTS] — 45 logical reads
  ⚡ Best by CPU:            [JOIN→EXISTS] — 10ms
  💾 Best by memory grant:  [JOIN→EXISTS] — 256 KB
  ⚠️  SpillToTempDb:         [NOLOCK]
```

Example output with `--runs 3`:

```
Test 1/7 [JOIN→EXISTS] (3 runs)
⏱️  Time: 0.0191s mean ± 0.0012s (median: 0.0188s)
⚡ CPU: 10ms mean ± 1ms (median: 10ms)
📊 IO: 45 logical reads (median), 0 physical reads (median)
💾 Memory grant: 256 KB
...

🏆 RANKING:
  ⏱️  Best by time (median):  [JOIN→EXISTS] — 0.0188s
  📊 Best by IO (median):     [JOIN→EXISTS] — 45 logical reads
  ⚡ Best by CPU (median):    [JOIN→EXISTS] — 10ms
  💾 Best by memory grant:   [JOIN→EXISTS] — 256 KB
```

---

## How It Works

1. **`main.py`** — orchestrator: loads the query, generates variants, runs benchmarks, displays per-variant metrics, saves results to `results.json`, saves execution plans to `plans/`, and prints the multi-criteria ranking.
2. **`query.sql`** — base SQL query to optimize.
3. **`variants.py`** — dynamically generates structural variants of the base query using `sqlglot` AST parsing. Parses any T-SQL query, detects structural patterns, and applies transformations:
   - `JOIN→EXISTS` — replaces INNER JOINs with correlated `WHERE EXISTS` subqueries
   - `NOLOCK` — adds `WITH (NOLOCK)` hint to all tables
   - `RECOMPILE` — appends `OPTION (RECOMPILE)` to force fresh execution plan
   - `IN→EXISTS` — replaces `IN (subquery)` with `EXISTS`
   - `OR→UNION ALL` — splits OR conditions into separate queries with `UNION ALL`
   - `DISTINCT→GROUP BY` — replaces `SELECT DISTINCT` with `GROUP BY`
   - `Subquery→CTE` — extracts subqueries from FROM clause into `WITH` CTEs
   - `JOIN reorder` — swaps the order of first and last INNER JOIN (when ≥2 JOINs)
   - `CROSS APPLY` — converts JOIN with subquery to `CROSS APPLY`
   - `HASH/MERGE/LOOP JOIN` — generates three variants with join-method hints
   - `Index suggestions` — prepends `-- Consider index on [schema].[table]([col])` comments based on WHERE/JOIN ON column analysis

   Each variant is labeled with its transformation name (e.g. `JOIN→EXISTS`, `HASH JOIN`). The maximum number of variants is controlled by the `MAX_VARIANTS` environment variable (default: `60`).
4. **`guardrails.py`** — static safety checks using `sqlglot` AST analysis. Runs before each variant is benchmarked:
   - **G1** `no_limit_added` — blocks variants that add `TOP N` / `LIMIT` not present in the base query
   - **G2** `no_where_removed` — blocks variants that drop the `WHERE` clause entirely (UNION variants are exempt)
   - **G4** `nolock_warning` — warns (does not block) variants using `WITH (NOLOCK)`

   See [GUARDRAILS.md](GUARDRAILS.md) for the full rule reference.
5. **`validator.py`** — runtime semantic validation. Before benchmarking, computes `SELECT COUNT(*) FROM (<variant>) AS _v` and compares it against the base query row count. Mismatches block the variant. Failures degrade gracefully (warning printed, variant not blocked).
6. **`runner.py`** — per-variant execution flow:
   - Clears buffer pool and plan cache (`DBCC DROPCLEANBUFFERS` / `DBCC FREEPROCCACHE`) — graceful degradation if permission missing
   - Enables `SET STATISTICS IO ON` and `SET STATISTICS TIME ON` to collect logical/physical reads and CPU/elapsed time from `cursor.messages`; if the ODBC driver does not populate messages (e.g. ODBC Driver 18), falls back to runtime stats extracted from the XML execution plan
   - With `collect_plan=True` (default): enables `SET STATISTICS XML ON` to capture the actual execution plan — graceful degradation if `SHOWPLAN` permission missing; also queries `sys.query_store_runtime_stats`; for multi-run benchmarks, subsequent runs use `collect_plan=False` to reduce overhead
   - Executes the query, measures wall-clock time
   - Returns a dict with all collected metrics
7. **`aggregator.py`** — pure aggregation for multi-run benchmarks:
   - `compute_stats(values)` — computes mean / median / stdev / min / max using `statistics` stdlib; returns `None` for empty input, `stdev=0.0` for single value
   - `aggregate_runs(run_results)` — aggregates `time` and `server_metrics` across N runs, preserves execution plan / Query Store from the first run, skips error runs
8. **`stats_parser.py`** — pure functions for parsing SQL Server diagnostic output:
   - `parse_io_stats(messages)` — regex parser for `SET STATISTICS IO` output; sums metrics across all tables (handles JOIN scenarios)
   - `parse_time_stats(messages)` — regex parser for `SET STATISTICS TIME` execution times (excludes parse/compile phase)
   - `parse_execution_plan(xml_string)` — XML parser for actual execution plan; extracts `MemoryGrant`, `SpillToTempDb` warnings, physical operator list, and runtime stats (`QueryTimeStats`, `RunTimeCountersPerThread`)
8. **`db.py`** — connection factory using ODBC Driver 17.

---

## Project Structure

```
AutoResearch_SQLServer/
├── main.py              # Entry point — orchestrator
├── query.sql            # Base SQL query to optimize
├── variants.py          # Query variant generator
├── guardrails.py        # Static AST safety checks (G1, G2, G4)
├── validator.py         # Runtime row count validation
├── runner.py            # Query executor: SET STATISTICS, metrics, Query Store
├── aggregator.py        # Pure aggregation: compute_stats(), aggregate_runs()
├── stats_parser.py      # Pure parsers: IO/TIME regex, XML plan
├── db.py                # SQL Server connection factory
├── GUARDRAILS.md        # Guardrail rule reference
├── tests/
│   ├── test_stats_parser.py  # Unit tests for parsers (no DB needed)
│   ├── test_variants.py      # Unit tests for variant generator
│   ├── test_db.py            # Unit tests for connection factory (mocked)
│   ├── test_guardrails.py    # Unit tests for guardrails module
│   ├── test_validator.py     # Unit tests for validator module (mocked DB)
│   └── test_aggregator.py    # Unit tests for aggregator module
├── plans/               # Actual execution plans as .sqlplan (generated)
├── .env.example         # Environment variable template (commit this)
├── pytest.ini           # pytest configuration
├── requirements-dev.txt # Dev dependencies (pytest, pytest-cov)
├── results.json         # Benchmark results with server-side metrics (generated)
├── LICENSE
└── README.md
```

### `results.json` format

**Single run (`--runs 1`, default)** — flat format, backward-compatible:

```json
{
  "label": "JOIN→EXISTS",
  "query": "SELECT ...",
  "time": 0.2694,
  "server_metrics": {
    "logical_reads": 689,
    "physical_reads": 0,
    "read_ahead_reads": 0,
    "lob_logical_reads": 0,
    "lob_physical_reads": 0,
    "cpu_time_ms": 15,
    "elapsed_time_ms": 267
  },
  "execution_plan_file": "plans/plan_variant_1.sqlplan",
  "query_store": {
    "avg_duration_us": 267000,
    "avg_cpu_time_us": 15000,
    "avg_logical_io_reads": 689.0,
    "avg_physical_io_reads": 0.0,
    "avg_memory_grant_kb": 8192
  },
  "warnings": [],
  "validation": {
    "is_valid": true,
    "base_count": 1234,
    "variant_count": 1234,
    "message": "OK"
  },
  "guardrail_warnings": []
}
```

**Multiple runs (`--runs N`)** — aggregated format with `raw_runs`:

```json
{
  "label": "JOIN→EXISTS",
  "query": "SELECT ...",
  "runs": 3,
  "time": {
    "mean": 0.1912,
    "median": 0.1884,
    "stdev": 0.0121,
    "min": 0.1801,
    "max": 0.2051
  },
  "server_metrics": {
    "cpu_time_ms": { "mean": 15.0, "median": 15.0, "stdev": 0.0, "min": 15, "max": 15 },
    "logical_reads": { "mean": 689.0, "median": 689.0, "stdev": 0.0, "min": 689, "max": 689 }
  },
  "execution_plan_file": "plans/plan_variant_1.sqlplan",
  "query_store": { ... },
  "warnings": [],
  "validation": { ... },
  "guardrail_warnings": [],
  "raw_runs": [
    { "time": 0.1801, "server_metrics": { "cpu_time_ms": 15, "logical_reads": 689 } },
    { "time": 0.1884, "server_metrics": { "cpu_time_ms": 15, "logical_reads": 689 } },
    { "time": 0.2051, "server_metrics": { "cpu_time_ms": 15, "logical_reads": 689 } }
  ]
}
```

> The `.sqlplan` files in `plans/` can be opened in **SQL Server Management Studio (SSMS)** for a visual execution plan view.

---

## Customizing Variants

`variants.py` uses `sqlglot` to parse the base query into an AST and applies a registry of transform functions. To add a new transformation, define a function following this pattern and add it to the `_TRANSFORMS` list:

```python
def _transform_my_hint(ast):
    # 1. Detect pattern — return [] if not applicable
    if not ast.find(exp.SomeNode):
        return []
    # 2. Copy the AST and modify the copy (never mutate the original)
    ast_c = ast.copy()
    # ... apply transformation ...
    return [("My hint label", ast_c)]
```

All transforms are automatically applied by the `generate_variants()` orchestrator. Transforms that don't detect their pattern return an empty list and are silently skipped.

The `MAX_VARIANTS` environment variable (default: `60`) caps the total number of variants per run. Set it to limit DB load for complex queries:

```bash
MAX_VARIANTS=20 python main.py
```

---

## Testing

```bash
# Install dev dependencies (includes pytest)
pip install -r requirements-dev.txt

# Run all tests
pytest

# Run with coverage report
pytest --cov=. --cov-report=term-missing
```

---

## License

This project is licensed under the [MIT License](LICENSE).