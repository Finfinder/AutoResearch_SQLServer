# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/),
and this project adheres to [Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added

- `TestCrossApply` class in `tests/test_variants.py` (3 tests) — covers `_transform_cross_apply`: produces `CROSS APPLY` label, SQL contains `CROSS APPLY` keyword, no variant produced when JOIN has no subquery

### Changed

- `variants.py` — Cognitive Complexity refactor across 4 functions (all now ≤15):
  - Extracted `_attach_exists_to_where(ast_c, exists_expr)` module-level helper — used by `_transform_join_to_exists` (CC: 17→14)
  - Extracted `_build_correlated_exists(inner_select, inner_col, outer_col)` module-level helper — used by `_transform_in_to_exists` (CC: 17→13)
  - Extracted `_full_table_name(tbl)`, `_build_alias_map(ast)`, `_collect_cols_from(node, alias_map, candidates)` as module-level helpers — replaces nested closure pattern in `_collect_index_candidates` (CC: 31→8)
  - Extracted `_apply_transforms(ast, transform_fns)` module-level helper — used by `generate_variants` (CC: 16→9)
- `tests/test_variants.py` — branch coverage of `variants.py` improved from 82% to 89% due to `TestCrossApply` addition

### Fixed

- `variants.py` — `_transform_join_hints`: join hints are now emitted as `OPTION (HASH JOIN / MERGE JOIN / LOOP JOIN)` query hints instead of inline `method` attribute on the JOIN node; the inline form generated invalid T-SQL (`HASH JOIN t2 ...` without the required `INNER` keyword), causing SQL Server error 102

### Changed (gitignore)

- `.gitignore` — added `plans/` and `results.json` to ignored paths; both are runtime-generated artefacts and should not be version-controlled
- Removed `plans/plan_variant_*.sqlplan` and `results.json` from git tracking (`git rm --cached`)

- `sqlglot>=26.0` dependency — SQL parser for AST-based query transformations (zero transitive dependencies)
- `VariantGenerationError` exception in `variants.py` with structured fields: `line`, `col`, `fragment`, `suggestion` — raised when the base query cannot be parsed; provides precise error location for diagnosis
- 8 new SQL transformations in `variants.py`: `IN→EXISTS`, `OR→UNION ALL`, `DISTINCT→GROUP BY`, `Subquery→CTE`, `JOIN reorder`, `CROSS APPLY`, `HASH/MERGE/LOOP JOIN hints`, `Index suggestions`
- `MAX_VARIANTS` environment variable (default: `60`) — caps the number of generated variants per run; prints a warning on truncation
- `"label"` field in `results.json` — each result entry now includes the transformation name (e.g. `"JOIN→EXISTS"`, `"HASH JOIN"`)

### Changed

- `variants.py` — full rewrite: replaced 4 hardcoded `str.replace()` transformations with a dynamic AST-based generator using `sqlglot`. Generator parses any T-SQL query, detects structural patterns, and applies transformations automatically. Interface changed from `list[str]` to `list[tuple[str, str]]` (label, SQL).
- `main.py` — updated to handle the new `list[tuple[str, str]]` interface: iterates as `(label, query)`, displays transformation labels in test headers and ranking, adds `"label"` to `results.json`, catches `VariantGenerationError` with `sys.exit(1)`, handles empty variant list gracefully
- `tests/test_variants.py` — full rewrite: 48 unit tests covering all 12 transforms (pattern detected / pattern absent), `VariantGenerationError` fields, `MAX_VARIANTS` limit, interface contract, and SQL validity via `sqlglot.parse_one()` round-trip


- `pytest.ini` — formal pytest configuration with `pythonpath = .` (eliminates `sys.path` hacks), `testpaths = tests`, and test discovery patterns consistent with existing test conventions
- `tests/test_variants.py` — 6 unit tests for `generate_variants()` covering all 4 structural transformations (JOIN→EXISTS, TOP, NOLOCK, RECOMPILE) and the no-match edge case; no mocks required (pure function)
- `tests/test_db.py` — 6 unit tests for `get_connection()` covering env var validation (missing all / missing one), default and custom ODBC driver, connection string format, and return value; `pyodbc.connect` is fully mocked — no real database connection required
- `stats_parser.py` — pure-function module for parsing SQL Server diagnostic output:
  - `parse_io_stats(messages)` — regex parser for `SET STATISTICS IO` output from `cursor.messages`; sums logical/physical/read-ahead/lob reads across all tables in the query (handles JOIN scenarios)
  - `parse_time_stats(messages)` — regex parser for `SET STATISTICS TIME` output; extracts CPU time and elapsed time from the "Execution Times" section (excludes parse/compile phase)
  - `parse_execution_plan(xml_string)` — XML parser for actual execution plans from `SET STATISTICS XML`; extracts `MemoryGrant` (KB), `SpillToTempDb` warnings, list of physical operators, and runtime stats (`QueryTimeStats` CPU/elapsed, `RunTimeCountersPerThread` IO summed across operators)
- `tests/test_stats_parser.py` — 20 unit tests covering all parser functions with sample SQL Server message data (no database required); includes single-table IO, multi-table JOIN IO summing, combined TIME messages, XML plan with/without SpillToTempDb, invalid XML and None inputs, and runtime stats extraction (CPU/elapsed time, IO summing across operators)
- `plans/` directory — created automatically at runtime; stores actual execution plans as `.sqlplan` files (openable in SSMS)
- `.env.example` — environment variable template documenting all required connection parameters
- `README.md` — added project documentation with badges, quick start, configuration guide, project structure, and variant customization instructions

### Changed

- `tests/test_stats_parser.py` — removed `sys.path.insert()` hack (lines 2–5); imports now resolved natively via `pythonpath = .` in `pytest.ini`
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
