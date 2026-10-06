# Data Comparator Tool — KPN Migration (Teradata → Microsoft Fabric)

## Overview

Automated data reconciliation tool to validate KPN's data migration from **Teradata ODS** (source) to **Microsoft Fabric ODS** (target). Uses a two-level comparison strategy — fast DuckDB pass on all tables, deep realdiff analysis only on failed tables.

---

## Architecture

```
LEFT (Source)                          RIGHT (Target)
Teradata ODS                           Microsoft Fabric ODS
      |                                       |
      v                                       v
   pyodbc + Teradata ODBC Driver         pyodbc + SQL Server ODBC Driver
      |                                       |
      +------------------+--------------------+
                         |
                         v
             JSON Config (tables, PKs, exclusions)
                         |
                         v
              ┌──────────────────────┐
              │  LEVEL 1 — DuckDB    │
              │  Fast comparison     │
              │  Row counts          │
              │  PK matching         │
              │  Field diffs         │
              └──────────┬───────────┘
                         |
               ┌─────────┴──────────┐
               |                    |
            PASS ✅              FAIL ❌
               |                    |
          JSON Report         ┌─────▼──────────────┐
          (no action)         │  LEVEL 2 — realdiff │
                              │  Deep row-by-row    │
                              │  Field-level diff   │
                              │  Encoding analysis  │
                              └─────────┬───────────┘
                                        |
                                        v
                              Detailed Diff JSON Report
                                        |
                         ┌──────────────┴──────────────┐
                         v                             v
                   Summary Dashboard            Record Diff View
                   (Pass/Fail/Deep)             (exact field changes)
```

---

## Tech Stack

| Component | Technology |
|---|---|
| Language | Python 3.11+ |
| Comparison Engine (Level 1) | DuckDB |
| Comparison Engine (Level 2) | realdiff |
| Source Connectivity | pyodbc + Teradata ODBC Driver |
| Target Connectivity | pyodbc + SQL Server ODBC Driver (Fabric) |
| Config Format | JSON |
| Reporting | JSON files + HTML Web Dashboard |
| Automation | GitHub Actions |

---

## ODBC Connections

### Source — Teradata ODS
```python
source_conn = pyodbc.connect(
    "DRIVER={Teradata};"
    "DBCName=<host>;"
    "UID=<user>;"
    "PWD=<pass>;"
    "DATABASE=ODS_SCHEMA"
)
```

### Target — Microsoft Fabric ODS
```python
target_conn = pyodbc.connect(
    "DRIVER={ODBC Driver 17 for SQL Server};"
    "Server=<workspace>.datawarehouse.fabric.microsoft.com;"
    "Database=ODS_FABRIC;"
    "UID=<user>;"
    "PWD=<pass>;"
    "Authentication=ActiveDirectoryPassword"
)
```

---

## JSON Configuration

```json
{
  "type": "ducky",
  "meta": {
    "run_id": "KPN-2026-10-01",
    "deep_diff_on_fail": true,
    "deep_diff_engine": "realdiff"
  },
  "left": {
    "connection": "odbc_teradata",
    "object": "ODS_SCHEMA.TABLE_NAME"
  },
  "right": {
    "connection": "odbc_fabric",
    "object": "ODS_FABRIC.TABLE_NAME"
  },
  "columns": {
    "pk": ["ROW_ID"],
    "excluded": ["LOAD_DATE", "BATCH_ID", "ETL_HASH"]
  },
  "realdiff": {
    "bisection_factor": 32,
    "bisection_threshold": 1000,
    "sample_size": 500
  }
}
```

---

## Comparison Logic

| Check | DuckDB (Level 1) | realdiff (Level 2) |
|---|---|---|
| Row count match | Yes | Yes |
| PK matching | Yes | Yes |
| Field value diff | Count only | **Exact value shown** |
| Encoding issues | Count only | **Exact character shown** |
| Null vs empty | Yes | Yes |
| Performance | Fast — runs on all tables | Slower — runs on FAIL tables only |
| Output | Summary JSON | Detailed diff JSON |

---

## Two-Level Strategy

### Level 1 — DuckDB (Fast, runs on all tables)
- Compares row counts between Teradata and Fabric
- Matches records using primary keys
- Counts field-level mismatches and encoding issues
- Runtime: seconds per table
- Output: Pass ✅ or Fail ❌ per table

### Level 2 — realdiff (Deep, runs only on FAIL tables)
- Exact row-by-row comparison on failed tables
- Shows precise field values that differ (left vs right)
- Identifies root cause — encoding bugs, null mapping, missing records
- Runtime: minutes per table
- Output: Detailed diff report per failed table

---

## Output Report Structure

### Summary JSON
```json
{
  "run_id": "KPN-2026-10-01",
  "statistics": {
    "left_total": 6634247,
    "right_total": 6634247,
    "matched_pk_records": 6634232,
    "different_records": 15,
    "encoding_mismatches": 383
  },
  "field_results": {
    "ADDR": 275,
    "CITY": 120,
    "ZIPCODE": 38
  }
}
```

### Web Dashboard
- Total tables: Pass / Fail / Error counts
- Per-table status, runtime, and mismatch count
- Drill-down to field-level and record-level diff

---

## Implementation Roadmap

| Phase | Deliverable | Timeline |
|---|---|---|
| Phase 1 | Teradata + Fabric ODBC connections + DuckDB basic comparison script | Week 1–2 |
| Phase 2 | JSON config system for KPN tables (PKs, exclusions, SQL filters) | Week 2–3 |
| Phase 3 | realdiff integration for FAIL tables + detailed diff output | Week 3–4 |
| Phase 4 | JSON reports + Web dashboard (Pass/Fail/Deep status) | Week 4–5 |
| Phase 5 | GitHub Actions for daily automated reconciliation runs | Week 5 |

---

## Prerequisites & Requirements

| Requirement | Details |
|---|---|
| DB Credentials | Teradata ODS host, user, password; Fabric workspace URL + Azure AD auth |
| Primary Key Mapping | PK column confirmed per table from KPN team |
| Initial Table List | Start with 5–10 tables before scaling to full schema |
| Network Access | Proxy/firewall access from run environment to both endpoints |
| Python Environment | Python 3.11+, pyodbc, duckdb, realdiff, pandas |

---

## Key Risks

| Risk | Mitigation |
|---|---|
| Teradata SQL dialect differences | Fetch data via ODBC, compare in ANSI SQL via DuckDB |
| Fabric Azure AD authentication | Use service principal or AAD token — not plain user/pass |
| Large Teradata table sizes | Add WHERE filter or row limit in config for initial runs |
| Encoding mismatches | realdiff level catches exact character-level differences |
| Network latency | Run inside the same environment/VPN as the databases |

---

## Business Value

> Instead of spending manual effort querying millions of rows across complex ETL pipelines, this tool automates data validation for KPN's Teradata → Fabric migration. It shifts the team's responsibility from manual checking to automated loading, testing, and debugging — saving hundreds of hours in QA and reducing migration risk.
