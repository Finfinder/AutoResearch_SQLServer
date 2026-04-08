# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/),
and this project adheres to [Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added

- `stats_parser.py` — pure-function module for parsing SQL Server diagnostic output:
  - `parse_io_stats(messages)` — regex parser for `SET STATISTICS IO` output from `cursor.messages`; sums logical/physical/read-ahead/lob reads across all tables in the query (handles JOIN scenarios)
  - `parse_time_stats(messages)` — regex parser for `SET STATISTICS TIME` output; extracts CPU time and elapsed time from the "Execution Times" section (excludes parse/compile phase)
  - `parse_execution_plan(xml_string)` — XML parser for actual execution plans from `SET STATISTICS XML`; extracts `MemoryGrant` (KB), `SpillToTempDb` warnings, list of physical operators, and runtime stats (`QueryTimeStats` CPU/elapsed, `RunTimeCountersPerThread` IO summed across operators)
- `tests/test_stats_parser.py` — 20 unit tests covering all parser functions with sample SQL Server message data (no database required); includes single-table IO, multi-table JOIN IO summing, combined TIME messages, XML plan with/without SpillToTempDb, invalid XML and None inputs, and runtime stats extraction (CPU/elapsed time, IO summing across operators)
- `plans/` directory — created automatically at runtime; stores actual execution plans as `.sqlplan` files (openable in SSMS)
- `.env.example` — environment variable template documenting all required connection parameters
- `README.md` — added project documentation with badges, quick start, configuration guide, project structure, and variant customization instructions

### Changed

- `runner.py` — `run_query()` now collects server-side diagnostics in addition to wall-clock time:
  - Enables `SET STATISTICS IO ON` and `SET STATISTICS TIME ON` before each query; parses IO and CPU/elapsed metrics from `cursor.messages` via `stats_parser`
  - Enables `SET STATISTICS XML ON` to capture the actual execution plan (iterates result sets via `while cursor.nextset()` loop to skip intermediate non-query sets); graceful degradation if `SHOWPLAN` permission is missing
  - Falls back to runtime stats from XML execution plan when `cursor.messages` is empty (ODBC Driver 18 behavior)
  - After execution, optionally queries `sys.query_store_runtime_stats` DMV for historical metrics; parameterized query (`?`) prevents SQL injection; graceful degradation if Query Store is disabled or permission is missing
  - Return type changed from `(duration, error)` tuple to a dict with keys: `time`, `error`, `server_metrics`, `execution_plan`, `plan_xml`, `query_store`, `warnings`
  - Wall-clock timing switched to `time.perf_counter()` for higher resolution and immunity to system clock adjustments
- `runner.py` — `run_query()` now executes `DBCC DROPCLEANBUFFERS` and `DBCC FREEPROCCACHE` before each benchmark measurement to ensure cold-cache conditions; requires `ALTER SERVER STATE` permission — if missing, a warning is printed and the benchmark continues without cache clearing (graceful degradation)
- `runner.py` — `run_query()` now closes the cursor and connection in a `finally` block, preventing connection leaks when queries fail or complete normally
- `main.py` — updated orchestrator for extended run results:
  - Per-variant output now includes server-side CPU time, IO reads, memory grant, and SpillToTempDb warnings
  - Saves actual execution plan XML to `plans/plan_variant_N.sqlplan` when available
  - Multi-criteria ranking at end of run: best by time, IO (logical reads), CPU, and memory grant
  - `results.json` extended with `server_metrics`, `execution_plan`, `execution_plan_file`, `query_store`, and `warnings` fields per variant
  - `pathlib.Path` used for `plans/` directory creation
- `query.sql` — updated base query to use AdventureWorks schema (`[Sales].[SalesOrderHeader]`, `[Sales].[Customer]`, `[CustomerID]`, `[OrderDate]`) with proper bracket notation
- `variants.py` — updated JOIN→EXISTS and NOLOCK variant transformations to match the new AdventureWorks table and column names
- `db.py` — added `TrustServerCertificate=yes` to connection string; credentials are now read from environment variables (`DB_SERVER`, `DB_DATABASE`, `DB_UID`, `DB_PWD`, `DB_DRIVER`) instead of being hardcoded
- `README.md` — updated Installation, Configuration, Quick Start and How It Works sections; added permissions table for graceful degradation; updated project structure with `stats_parser.py`, `tests/`, `plans/`
- `requirements.txt` — added `python-dotenv>=1.0.0`

### Fixed

- `runner.py` — `_fetch_query_store()` LIKE pattern now correctly matches queries containing bracket-quoted identifiers (e.g. `[Sales].[SalesOrderHeader]`): square brackets are escaped as `[[]`, and whitespace sequences are replaced with `%` wildcards to match regardless of `\r\n` vs `\n` vs space differences between the submitted query and the text stored by Query Store
- `stats_parser.py` — extracted `_extract_runtime_stats(root, ns)` helper from `parse_execution_plan()` to reduce Cognitive Complexity to within SonarQube limit
- `runner.py` — extracted `_clear_cache(cursor)` and `_collect_execution_plan(cursor, warnings)` helpers from `run_query()` to reduce Cognitive Complexity to within SonarQube limit

### Security

- Eliminated hardcoded SQL Server credentials that triggered SonarQube violations `python:S2068` (Credentials should not be hard-coded) and `secrets:S6703` (Database passwords should not be disclosed)

## [0.1.0] - 2026-04-04

### Added

- `main.py` — orchestrator: loads base query, generates variants, benchmarks each, saves results to `results.json`, reports the fastest
- `query.sql` — sample base query (orders JOIN customers with date filter)
- `variants.py` — query variant generator with 4 transformations: JOIN→EXISTS, TOP N, WITH (NOLOCK), OPTION (RECOMPILE)
- `runner.py` — query executor measuring wall-clock time via `pyodbc`, returns `(duration, error)` tuple
- `db.py` — SQL Server connection factory using ODBC Driver 17
- `LICENSE` — MIT license
