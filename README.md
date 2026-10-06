# KPN Data Bridge — Setup & Usage Guide

Automated data reconciliation tool that validates the migration from **Teradata ODS** (source) to **Microsoft Fabric ODS** (target).  
Two-level strategy: fast hash-based pass on all tables → deep row-by-row diff only on tables that fail.

---

## How It Works

```
Teradata ODS  ──┐
                ├──► Level 1: DuckDB hash comparison ──► PASS ✅  (done)
Fabric ODS    ──┘                                    └──► FAIL ❌  (triggers Level 2)
                                                              │
                                                              ▼
                                               Level 2: Deep diff — exact left vs right
                                                              │
                                                              ▼
                                                    JSON Report + HTML Dashboard
```

---

## Prerequisites

| Requirement | Version | Notes |
|---|---|---|
| Python | 3.11+ | [python.org](https://www.python.org/downloads/) |
| Teradata ODBC Driver | 16.20 / 17.00 / 20.00 | Auto-detected — falls back to pure Python driver if not installed |
| ODBC Driver 17 for SQL Server | Any | Required for Fabric Service Principal auth |

> **Tip:** If you don't have the Teradata ODBC driver, the tool will automatically fall back to `teradatasql` (pure Python). It still works — no manual driver install needed.

---

## Step 1 — Clone & Install

```bash
# Clone the repository
git clone <repo-url>
cd kpn-data-bridge

# Install Python dependencies
pip install -r requirements.txt
```

---

## Step 2 — Create Your Customer Profile

Each customer gets their own folder under `profiles/`. This means **zero code changes** when switching between customers — only the `--profile` argument changes.

### 2a. Create the profile folder

```
profiles/
└── <your-customer-name>/
    ├── connections.json    ← maps connection names to env var names
    ├── .env                ← actual credentials (never committed to git)
    └── configs/
        └── table1.json     ← one config file per table comparison
```

### 2b. Create `profiles/<customer>/connections.json`

This file tells the tool **which environment variables** to read for each connection.  
Use a unique prefix per customer so credentials don't collide.

```json
{
  "odbc_teradata": {
    "type": "teradata",
    "host_env":     "KPN_TERADATA_HOST",
    "user_env":     "KPN_TERADATA_USER",
    "pass_env":     "KPN_TERADATA_PASS",
    "database_env": "KPN_TERADATA_DB"
  },
  "odbc_fabric": {
    "type": "fabricsql",
    "server_env":        "KPN_FABRIC_SERVER",
    "database_env":      "KPN_FABRIC_DB",
    "tenant_id_env":     "KPN_FABRIC_TENANT_ID",
    "client_id_env":     "KPN_FABRIC_CLIENT_ID",
    "client_secret_env": "KPN_FABRIC_CLIENT_SECRET"
  }
}
```

> **Supported connection types:** `teradata`, `teradatasql`, `sqlserver`, `fabricsql`

### 2c. Create `profiles/<customer>/.env`

Fill in the actual credentials. This file is **gitignored** — it never gets committed.

```dotenv
# Teradata
KPN_TERADATA_HOST=your-teradata-host.example.com
KPN_TERADATA_USER=your_username
KPN_TERADATA_PASS=your_password
KPN_TERADATA_DB=your_database

# Microsoft Fabric (Service Principal)
KPN_FABRIC_SERVER=your-workspace.datawarehouse.fabric.microsoft.com
KPN_FABRIC_DB=Your_Warehouse
KPN_FABRIC_TENANT_ID=xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx
KPN_FABRIC_CLIENT_ID=xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx
KPN_FABRIC_CLIENT_SECRET=your-client-secret
```

> **Where to find these values:**
> - Teradata: ask your DBA for the DBCName/host, username, password, and default database.
> - Fabric: in the Azure portal → App Registrations → your service principal. The SQL endpoint is in Fabric workspace settings → SQL connection string.

---

## Step 3 — Verify Connections

Before running any comparison, confirm both databases are reachable:

```bash
python scripts/test_connections.py --profile kpn
```

Expected output:
```
[odbc_teradata]  ✅  Connected — Teradata Database ODBC Driver 20.00
[odbc_fabric]    ✅  Connected — ODBC Driver 17 for SQL Server
```

If you see ❌, check your `.env` values and network access.

---

## Step 4 — Create a Table Config

Each table comparison is driven by a JSON config file in `profiles/<customer>/configs/`.

```json
{
  "type": "ducky",
  "meta": {
    "run_id": "KPN-2026-10-01",
    "deep_diff_on_fail": true,
    "deep_diff_engine": "deepdiff"
  },
  "left": {
    "connection": "odbc_teradata",
    "object": "ODS_SCHEMA.TABLE_NAME",
    "condition": null
  },
  "right": {
    "connection": "odbc_fabric",
    "object": "dbo.TABLE_NAME",
    "condition": null
  },
  "columns": {
    "pk": ["ROW_ID"],
    "excluded": ["LOAD_DATE", "BATCH_ID", "ETL_HASH"]
  },
  "realdiff": {
    "bisection_factor": 32,
    "bisection_threshold": 1000,
    "sample_size": 500
  },
  "performance": {
    "chunk_size": 100000,
    "row_limit": null,
    "max_diff_pks": 10000
  }
}
```

| Field | Description |
|---|---|
| `run_id` | Label for this run — appears in report filenames and the dashboard |
| `deep_diff_on_fail` | Set `true` to automatically run Level 2 when Level 1 fails |
| `left.object` | Fully qualified table name on Teradata (`DATABASE.TABLE`) |
| `right.object` | Fully qualified table name on Fabric (`schema.TABLE`) |
| `condition` | Optional SQL WHERE predicate (e.g. `"STATUS <> 'D'"` to exclude soft-deleted rows) |
| `pk` | Primary key column(s) — used to join both sides |
| `excluded` | Columns to skip (audit/ETL metadata columns like LOAD_DATE, BATCH_ID) |
| `sample_size` | Max number of differing rows shown in the deep diff report |
| `row_limit` | Set to a number (e.g. `10000`) for a quick smoke test before a full run |

---

## Step 5 — Run a Comparison

### Single table

```bash
python scripts/run_comparison.py --profile kpn --config table1.json
```

### All tables in the profile (recommended)

```bash
python scripts/run_batch.py --profile kpn
```

Run in parallel (N workers):

```bash
python scripts/run_batch.py --profile kpn --workers 4
```

---

## Step 6 — View the Dashboard

After the run completes, open the dashboard in any browser:

```
ui/index.html
```

Double-click the file or drag it into a browser window — no server needed.

### Dashboard walkthrough

**Summary tiles (top row)**

| Tile | Meaning |
|---|---|
| Reports | Total number of tables compared |
| OK | Tables where source and target match exactly |
| Fail | Tables with at least one difference |
| Error | Tables that could not be compared (connection / SQL error) |
| Left / Right Total | Total row counts across all tables |
| Different Records | Rows present on both sides but with at least one field value difference |
| Encoding Mismatches | Rows where a field difference is caused by special character encoding loss |

**Table grid (click any row to expand)**

Click a table row to see:
- Row count breakdown (matched, identical, different, left-only, right-only)
- Per-column mismatch counts
- Encoding mismatches by column
- Deep diff — exact `Left (Source)` vs `Right (Target)` values for each differing record

**Analysis Guide — Points of Consideration**

Click **Show ▾** below the summary tiles to open the Analysis Guide. It explains each type of finding and what action to take — from critical missing rows down to trailing spaces.

---

## Step 7 — Interpret the Results

| Finding | What it means | What to do |
|---|---|---|
| **Left Unique > 0** | Rows in Teradata not found in Fabric | Re-run migration for affected PKs |
| **Right Unique > 0** | Rows in Fabric not in Teradata | Investigate unintended inserts or scope mismatch |
| **Encoding mismatches** | ü→u, é→e, ë→e etc. | Fix character set (UTF-8) in ETL pipeline |
| **Field mismatches** | Value differs for the same PK | Check ETL transformation logic for that column |
| **NULL vs (empty)** | Source has NULL, target has `''` | Align COALESCE / NULLIF handling in ETL |
| **Trailing space (·)** | Invisible whitespace difference | Apply TRIM() in ETL or target load |
| **PASS** | Source and target match exactly | No action needed |

---

## Folder Structure

```
kpn-data-bridge/
├── profiles/
│   └── kpn/
│       ├── connections.json   ← connection → env var mapping
│       ├── .env               ← credentials (gitignored)
│       └── configs/           ← one JSON file per table
├── comparators/
│   ├── ducky.py               ← Level 1 (DuckDB hash comparison)
│   └── deepdiff.py            ← Level 2 (row-by-row field diff)
├── connections/
│   └── factory.py             ← builds ODBC connections from env vars
├── reports/                   ← JSON reports written here after each run
├── ui/
│   └── index.html             ← HTML dashboard (open in any browser)
├── scripts/
│   ├── test_connections.py    ← verify connectivity before running
│   ├── setup_test_data.py     ← create test tables on both sides
│   ├── run_comparison.py      ← single table run
│   └── run_batch.py           ← all tables in a profile
└── requirements.txt
```

---

## Quick Reference — Common Commands

```bash
# Test connections
python scripts/test_connections.py --profile kpn

# Create test tables (first-time setup / smoke test)
python scripts/setup_test_data.py --profile kpn

# Run a single table
python scripts/run_comparison.py --profile kpn --config my_table.json

# Run all tables
python scripts/run_batch.py --profile kpn

# Run all tables with 4 parallel workers
python scripts/run_batch.py --profile kpn --workers 4

# Open the dashboard
start ui/index.html        # Windows
open  ui/index.html        # Mac
```

---

## Adding a New Customer

1. Create `profiles/<new-customer>/` folder
2. Add `connections.json` with that customer's env var names (use their prefix, e.g. `ACME_`)
3. Add `.env` with their actual credentials
4. Add table configs under `profiles/<new-customer>/configs/`
5. Run with `--profile <new-customer>`

No code changes required.

---

## GitHub Actions (Automated Daily Run)

The workflow at `.github/workflows/daily_compare.yml` runs automatically at **06:00 UTC every day**.

Add credentials as **repository secrets** in GitHub → Settings → Secrets and variables → Actions:

```
KPN_TERADATA_HOST, KPN_TERADATA_USER, KPN_TERADATA_PASS, KPN_TERADATA_DB
KPN_FABRIC_SERVER, KPN_FABRIC_DB, KPN_FABRIC_TENANT_ID,
KPN_FABRIC_CLIENT_ID, KPN_FABRIC_CLIENT_SECRET
```

Reports are saved as workflow artifacts (30-day retention) and a summary is posted to the job log.
