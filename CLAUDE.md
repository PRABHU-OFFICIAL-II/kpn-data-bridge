# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Purpose

Data reconciliation tool to validate KPN's migration from **Teradata ODS** (source/left) to **Microsoft Fabric ODS** (target/right). Modelled after a BMW Oracle→Snowflake implementation. Two-level strategy: fast DuckDB pass on all tables, deep row-by-row diff only on tables that fail.

## Intended Project Structure

```
kpn-data-bridge/
├── config/               # Per-table JSON configs (one file per table or grouped)
├── connections/          # ODBC connection factory (teradata + fabric)
├── comparators/
│   ├── ducky.py          # Level 1 — DuckDB fast comparison
│   └── deepdiff.py       # Level 2 — row-by-row diff (realdiff replacement)
├── reports/              # Output JSON reports written here at runtime
├── ui/                   # HTML dashboard reading from reports/
├── scripts/              # Entry-point CLI scripts
├── tool/                 # Packaged wheels (e.g. realdiff if obtained)
├── .env.example          # Template for required env vars (no real values)
└── requirements.txt
```

## Configuration Format

Each table comparison is driven by a JSON config file:

```json
{
  "type": "ducky",
  "meta": { "run_id": "KPN-2026-10-01", "deep_diff_on_fail": true, "deep_diff_engine": "realdiff" },
  "left":  { "connection": "odbc_teradata", "object": "ODS_SCHEMA.TABLE_NAME", "condition": "optional WHERE clause" },
  "right": { "connection": "odbc_fabric",   "object": "ODS_FABRIC.TABLE_NAME",  "condition": "optional WHERE clause" },
  "columns": { "pk": ["ROW_ID"], "excluded": ["LOAD_DATE", "BATCH_ID", "ETL_HASH"] },
  "realdiff": { "bisection_factor": 32, "bisection_threshold": 1000, "sample_size": 500 }
}
```

`condition` is an optional SQL WHERE predicate injected when fetching data. `excluded` columns are omitted from comparison (audit/ETL metadata columns).

## Credentials

All database credentials are environment variables — never in config files or committed code.

| Env Var | Purpose |
|---|---|
| `TERADATA_HOST` | Teradata DBCName/host |
| `TERADATA_USER` | Teradata username |
| `TERADATA_PASS` | Teradata password |
| `FABRIC_SERVER` | Fabric workspace SQL endpoint |
| `FABRIC_DB` | Fabric database name |
| `FABRIC_USER` | Fabric username (or service principal ID) |
| `FABRIC_PASS` | Fabric password / secret |

ODBC connection strings are assembled in `connections/` from these env vars at runtime.

## Two-Level Comparison

**Level 1 (DuckDB — `comparators/ducky.py`):**
- Fetches both sides via ODBC into DuckDB in-memory tables
- Compares row counts, PK matches, field-level mismatch counts
- Output: PASS ✅ or FAIL ❌ + summary JSON
- Runtime: seconds per table

**Level 2 (deep diff — `comparators/deepdiff.py`):**
- Runs only on tables that fail Level 1
- Exact row-by-row comparison, shows precise left vs right values
- Identifies encoding issues (e.g. European characters), null mapping, missing records
- Output: detailed diff JSON per table

## Output Report Format

Each table produces a JSON report under `reports/`. A combined `group-reports/` summary aggregates all tables. The HTML dashboard (`ui/`) reads the combined report and renders:
- Summary stats: total REPORTS, OK, FAIL, ERROR counts
- Per-table grid: table name, type, status, runtime, left/right row counts, diff count, encoding mismatches

## Key Implementation Notes

- Fabric (Microsoft SQL Server) requires `Authentication=ActiveDirectoryPassword` or service principal auth — plain username/password auth may not work depending on the tenant configuration.
- Teradata uses `DRIVER={Teradata}` with `DBCName=` (not `Server=`).
- DuckDB comparison loads data as DataFrames or directly via `duckdb.connect()` — do not stream row-by-row in Level 1 as it defeats the purpose.
- `realdiff` in the BMW implementation is a **proprietary wheel** (not on PyPI). If unavailable, `comparators/deepdiff.py` implements equivalent bisection-based diff logic natively.
- The `condition` field in config maps to a SQL WHERE clause appended when querying each side — useful for filtering soft-deleted rows before comparison.
- GitHub Actions workflow runs daily, triggers jobs per config file, writes JSON reports as artifacts.
