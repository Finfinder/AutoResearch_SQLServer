# AutoResearch SQL Server

[![Python 3.10+](https://img.shields.io/badge/Python-3.10+-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![SQL Server](https://img.shields.io/badge/SQL%20Server-ODBC%2017-CC2927?logo=microsoftsqlserver&logoColor=white)](https://learn.microsoft.com/en-us/sql/connect/odbc/microsoft-odbc-driver-for-sql-server)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

Automated SQL query performance researcher for Microsoft SQL Server. Takes a base SQL query, generates structural variants (JOIN→EXISTS, TOP, NOLOCK, RECOMPILE), benchmarks each against a live database, and reports the fastest one.

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
- Python packages: `pyodbc`, `python-dotenv`

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

> **Production / Docker**: set the variables directly in the environment (container runtime, secrets manager, CI/CD). Values from the environment always take priority over `.env`.

> **Security**: `.env` is listed in `.gitignore` and must never be committed to the repository.

Place your base SQL query in `query.sql`:

```sql
SELECT o.*
FROM orders o
JOIN customers c ON o.customer_id = c.id
WHERE o.created_at > '2024-01-01'
```

---

## Quick Start

```bash
python main.py
```

The tool will:
1. Load the base query from `query.sql`
2. Generate structural variants via `variants.py`
3. Execute each variant against the database and measure execution time
4. Save all results to `results.json`
5. Print the fastest query with its execution time

Example output:

```
Test 1/4
⏱️ Time: 0.0312s
Test 2/4
⏱️ Time: 0.0187s
Test 3/4
⏱️ Time: 0.0245s
Test 4/4
⏱️ Time: 0.0298s

🏆 BEST RESULT:
0.0187s
SELECT TOP 1000 o.*
FROM orders o
JOIN customers c ON o.customer_id = c.id
WHERE o.created_at > '2024-01-01'
```

---

## How It Works

1. **`main.py`** — orchestrator: loads the query, generates variants, runs benchmarks, saves results, reports the winner.
2. **`query.sql`** — base SQL query to optimize.
3. **`variants.py`** — generates structural variants of the base query using string transformations:
   - `JOIN` → `EXISTS` subquery
   - Adding `TOP N` to limit result set
   - `WITH (NOLOCK)` hint (dirty reads, use with caution)
   - `OPTION (RECOMPILE)` to force fresh execution plan
4. **`runner.py`** — executes a query against SQL Server via `pyodbc`, measures wall-clock time, returns `(duration, error)`.
5. **`db.py`** — connection factory using ODBC Driver 17.

---

## Project Structure

```
AutoResearch_SQLServer/
├── main.py          # Entry point — orchestrator
├── query.sql        # Base SQL query to optimize
├── variants.py      # Query variant generator
├── runner.py        # Query executor with timing
├── db.py            # SQL Server connection factory
├── .env.example     # Environment variable template (commit this)
├── results.json     # Benchmark results (generated)
├── LICENSE
└── README.md
```

---

## Customizing Variants

Edit `generate_variants()` in `variants.py` to add custom transformations. Each variant is a string transformation of the base query:

```python
def generate_variants(base_query):
    variants = []

    # Add your transformations here
    variants.append(base_query.replace(
        "JOIN customers c ON o.customer_id = c.id",
        "WHERE EXISTS (SELECT 1 FROM customers c WHERE c.id = o.customer_id)"
    ))

    return variants
```

The transformations are query-specific — adapt them to match the structure of your base query in `query.sql`.

---

## License

This project is licensed under the [MIT License](LICENSE).