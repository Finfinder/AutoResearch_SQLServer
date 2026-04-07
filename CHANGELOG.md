# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/),
and this project adheres to [Semantic Versioning](https://semver.org/).

## [Unreleased]

### Changed

- `runner.py` — `run_query()` now executes `DBCC DROPCLEANBUFFERS` and `DBCC FREEPROCCACHE` before each benchmark measurement to ensure cold-cache conditions; requires `ALTER SERVER STATE` permission — if missing, a warning is printed and the benchmark continues without cache clearing (graceful degradation)

- `query.sql` — updated base query to use AdventureWorks schema (`[Sales].[SalesOrderHeader]`, `[Sales].[Customer]`, `[CustomerID]`, `[OrderDate]`) with proper bracket notation
- `variants.py` — updated JOIN→EXISTS and NOLOCK variant transformations to match the new AdventureWorks table and column names
- `db.py` — added `TrustServerCertificate=yes` to connection string to support SSL certificate trust for SQL Server connections without a valid certificate chain
- `db.py` — credentials are now read from environment variables (`DB_SERVER`, `DB_DATABASE`, `DB_UID`, `DB_PWD`, `DB_DRIVER`) instead of being hardcoded; `python-dotenv` loads `.env` automatically for local development
- `README.md` — updated Installation section (use `pip install -r requirements.txt`) and Configuration section (`.env` workflow with variable reference table; removed hardcoded connection string example)
- `requirements.txt` — added `python-dotenv>=1.0.0`

### Added

- `.env.example` — environment variable template documenting all required connection parameters
- `README.md` — project documentation with badges, quick start, configuration guide, project structure, and variant customization instructions

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
