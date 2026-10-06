# KPN Data Bridge

> Automated data reconciliation tool that validates KPN's migration from **Teradata ODS** (source) to **Microsoft Fabric ODS** (target).

---

## What This Tool Does

When migrating millions or billions of rows between databases, manual validation is impossible. KPN Data Bridge automates the entire process — it connects directly to both systems, compares the data, and produces a clear report telling you exactly:

- Which rows are **missing** from the target
- Which rows appear in the target but **not in the source**
- Which columns have **different values** for the same row
- Whether differences are caused by **encoding loss** (ü→u, é→e) or actual data problems
- Whether **NULL vs empty string** or **trailing spaces** are causing invisible mismatches

Results are written to JSON reports and displayed in an **HTML dashboard** that any analyst can open in a browser — no server, no setup.

---

## How It Works — Two-Level Strategy

```
Teradata ODS  ──┐
                ├──► Level 1: Hash comparison (all tables, seconds each)
Fabric ODS    ──┘        │
                         ├──► PASS ✅  No differences — done
                         └──► FAIL ❌  Differences found
                                  │
                                  ▼
                         Level 2: Deep diff
                         (only on failed tables, only on mismatched rows)
                                  │
                                  ▼
                         Exact field-level report:
                         Left (Teradata) vs Right (Fabric)
                                  │
                                  ▼
                         JSON Report + HTML Dashboard
```

### Level 1 — Hash Comparison (Fast)
- Computes an MD5 fingerprint of every row on both sides using all compared columns
- One join compares the fingerprints — identical hash means identical row, skip it
- Finds all differing rows in a **single pass**, regardless of how many columns the table has
- A 100-column table costs the same as a 5-column table

### Level 2 — Deep Diff (Precise)
- Runs **only on tables that fail Level 1**
- Fetches **only the mismatched rows** by PK — not the whole table again
- Shows exact `Left value → Right value` for every differing field
- Also shows the full row for records missing on either side

> **Scale example:** A 100-million-row table with 500 differences — Level 1 does one hash pass over 100M rows, Level 2 fetches only those 500 rows. Field-level analysis runs on 500 rows, not 100 million.

---

## What Differences Are Detected

| Difference Type | How It's Detected | Where It Appears |
|---|---|---|
| Row missing from Fabric | Level 1 — left_unique count | Dashboard → Left-only rows (exact missing row shown) |
| Row missing from Teradata | Level 1 — right_unique count | Dashboard → Right-only rows (exact phantom row shown) |
| Column value differs | Level 1 — hash mismatch | Dashboard → Deep Diff (exact left vs right per field) |
| Encoding loss (ü→u, é→e) | Level 1 — non-ASCII check on mismatched rows | Dashboard → Encoding Mismatches by Field |
| NULL vs empty string | Level 1 — hash treats them differently | Dashboard → Deep Diff shows `(null)` vs `(empty)` |
| Trailing spaces | Level 1 — hash includes whitespace | Dashboard → Deep Diff shows `value·` marker |
| Numeric/decimal precision | Level 1 — hash on exact value | Dashboard → Deep Diff shows exact numbers |
| Row count mismatch | Level 1 — left/right total counts | Dashboard → Summary tiles |

---

## Prerequisites

| Requirement | Version | Notes |
|---|---|---|
| Python | 3.11+ | [python.org](https://www.python.org/downloads/) |
| Teradata ODBC Driver | 16.20 / 17.00 / 20.00 | Auto-detected — falls back to pure Python if not installed |
| ODBC Driver 17 for SQL Server | Any | Required for Fabric Service Principal auth |

> **Note on Teradata driver:** If no ODBC driver is installed, the tool automatically falls back to `teradatasql` (pure Python). No manual driver installation required.

---

## Step 1 — Clone & Install

```bash
git clone https://github.com/PRABHU-OFFICIAL-II/kpn-data-bridge.git
cd kpn-data-bridge

pip install -r requirements.txt
```

---

## Step 2 — Create Your Customer Profile

Each customer gets their own folder under `profiles/`. **Zero code changes** are needed when switching between customers — only the `--profile` argument changes.

```
profiles/
└── <customer-name>/
    ├── connections.json    ← maps connection names to env var names
    ├── .env                ← actual credentials (gitignored — never committed)
    └── configs/
        └── table1.json     ← one config file per table comparison
```

### 2a — `connections.json`

Tells the tool which environment variables hold the credentials for each connection. Use a customer-specific prefix (e.g. `KPN_`) so credentials from different customers never collide.

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

**Supported connection types:** `teradata`, `teradatasql`, `sqlserver`, `fabricsql`

### 2b — `.env`

The actual credentials. This file is **gitignored** — it is never committed to the repository.

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
> - **Teradata:** Ask your DBA for the DBCName/host, username, password, and default database name.
> - **Fabric:** Azure portal → App Registrations → your service principal. SQL endpoint is in Fabric workspace settings → SQL connection string.

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

If you see ❌ — check the `.env` values and confirm network access to both hosts.

---

## Step 4 — Create a Table Config

Each table comparison is driven by a JSON config file under `profiles/<customer>/configs/`.

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
| `deep_diff_on_fail` | `true` = automatically run Level 2 when Level 1 fails |
| `left.object` | Fully qualified table name on Teradata (`DATABASE.TABLE`) |
| `right.object` | Fully qualified table name on Fabric (`schema.TABLE`) |
| `condition` | Optional SQL WHERE clause applied before fetching (e.g. `"STATUS <> 'D'"` to exclude soft-deleted rows) |
| `pk` | Primary key column(s) — supports composite PKs e.g. `["COL1", "COL2"]` |
| `excluded` | Columns to skip entirely — audit/ETL metadata that is intentionally different (LOAD_DATE, BATCH_ID, ETL_HASH) |
| `sample_size` | Max differing rows shown in the deep diff report (default 500) |
| `row_limit` | Limit to N rows for a quick smoke test before a full run — set `null` for production |
| `chunk_size` | ODBC fetch chunk size — controls memory usage (default 100,000 rows per chunk) |
| `max_diff_pks` | Max differing PKs collected for Level 2 (default 10,000) |

---

## Step 5 — Run a Comparison

### Single table

```bash
python scripts/run_comparison.py --profile kpn --config table1.json
```

### All tables in the profile

```bash
python scripts/run_batch.py --profile kpn
```

### All tables in parallel

```bash
python scripts/run_batch.py --profile kpn --workers 4
```

### What the output looks like

```
Opening connection: odbc_teradata  ✅
Opening connection: odbc_fabric    ✅

LEVEL 1 — DuckDB comparison
  Fetching LEFT:  ODS_SCHEMA.TABLE_NAME  →  11 rows
  Fetching RIGHT: dbo.TABLE_NAME         →  11 rows
  Loading into DuckDB...
  Row counts — left: 11  right: 11
  Left-only PKs: 1  Right-only PKs: 1
  Computing row hashes over 6 column(s)...
  Hash-diff rows: 5  (out of 10 matched PKs)
  Field-level diff on 5 differing rows across 6 column(s)...
    AMOUNT        1 mismatch
    CITY          2 mismatches
    FULL_NAME     2 mismatches  ← encoding
  Status: FAIL  Runtime: 2.8s

LEVEL 2 — Deep diff
  Fetching 5 differing rows from LEFT...
  Fetching 5 differing rows from RIGHT...
  Fetching 1 left-only rows...
  Fetching 1 right-only rows...
  Deep diff complete — 5 diff records resolved  runtime: 1.8s

Report: reports/report_ods_schema_table_name_kpn-2026-10-01.json
```

---

## Step 6 — View the Dashboard

Open in any browser — no server required:

```bash
# Windows
start ui/index.html

# Mac
open ui/index.html
```

Or simply double-click `ui/index.html` in File Explorer.

### Dashboard Sections

**Summary tiles** — at-a-glance counts across all tables:

| Tile | Meaning |
|---|---|
| Reports | Total tables compared |
| OK | Tables where source and target match exactly |
| Fail | Tables with at least one difference |
| Error | Tables that could not be compared (connection/SQL error) |
| Left / Right Total | Total row counts across all tables |
| Different Records | Rows present on both sides with at least one field difference |
| Encoding Mismatches | Field differences caused by special character encoding loss |

**Table grid** — one row per table. Click any row to expand full detail:
- Row count breakdown: matched, identical, different, left-only, right-only
- Field Mismatches — per-column mismatch counts
- Encoding Mismatches — columns where special characters were lost
- Deep Diff — exact `Left (Source / Teradata)` vs `Right (Target / Fabric)` for every differing record

**Special value rendering in diff view:**
- `NULL` → shown as `(null)` in muted italic
- `""` empty string → shown as `(empty)` in muted italic — never blank/invisible
- Trailing space → shown as `value·` with a yellow dot marker + `[trailing space]` label

**Analysis Guide** — click **Show ▾** to expand. Built-in reference for analysts explaining every finding type, what it means, and what action to take.

---

## Step 7 — Interpret the Results

| Finding | What it means | Recommended action |
|---|---|---|
| **Left Unique > 0** | Rows in Teradata not found in Fabric | Re-run ETL migration for the affected PKs |
| **Right Unique > 0** | Rows in Fabric not in Teradata | Investigate unintended inserts or scope mismatch |
| **Encoding mismatches** | ü→u, é→e, ë→e etc. | Fix character set (UTF-8) end-to-end in the ETL pipeline |
| **Field mismatches** | Column value differs for the same PK | Check ETL transformation logic for each flagged column |
| **NULL vs (empty)** | Source has NULL, target has `''` | Align COALESCE / NULLIF handling in ETL |
| **Trailing space (·)** | Invisible whitespace on one side | Apply TRIM() / RTRIM() in ETL or target load step |
| **Numeric diff** | Decimal precision or rounding difference | Verify DECIMAL(p,s) precision is identical on both sides |
| **PASS** | Source and target match exactly | No action needed ✅ |

---

## First-Time Smoke Test

To validate the full pipeline end-to-end before running production tables:

```bash
# Creates KPN_COMPARISON_TEST on both Teradata and Fabric
# with 11 rows covering all 7 difference scenarios
python scripts/setup_test_data.py --profile kpn

# Run the comparison on the test table
python scripts/run_comparison.py --profile kpn --config kpn_comparison_test.json

# Open dashboard to see results
start ui/index.html
```

The test covers:
| Scenario | PersonID | Expected result |
|---|---|---|
| Clean matching rows | 1–5 | PASS |
| Encoding loss (ü→u, é→e) | 6–7 | FAIL — encoding_mismatches_by_field |
| Exists in Teradata only | 8 | FAIL — left_unique |
| Exists in Fabric only | 9 | FAIL — right_unique |
| AMOUNT value differs | 10 | FAIL — field_results.AMOUNT |
| NULL vs empty string | 11 | FAIL — field_results.EMAIL |
| Trailing space | 12 | FAIL — field_results.POSTAL_CODE |

---

## Folder Structure

```
kpn-data-bridge/
├── profiles/
│   └── kpn/
│       ├── connections.json       ← connection → env var mapping
│       ├── .env                   ← credentials (gitignored)
│       └── configs/               ← one JSON file per table
├── comparators/
│   ├── ducky.py                   ← Level 1 (DuckDB hash-first comparison)
│   └── deepdiff.py                ← Level 2 (exact row-by-row field diff)
├── connections/
│   └── factory.py                 ← builds ODBC/teradatasql connections from env vars
├── reports/                       ← JSON reports written here at runtime (gitignored)
├── ui/
│   ├── index.html                 ← HTML dashboard (open in any browser)
│   └── report_data.js             ← auto-generated by aggregator
├── scripts/
│   ├── test_connections.py        ← verify connectivity before running
│   ├── setup_test_data.py         ← create end-to-end smoke test tables
│   ├── run_comparison.py          ← single table run
│   └── run_batch.py               ← all tables in a profile
├── .env.example                   ← credential template for new customers
├── requirements.txt
└── README.md
```

---

## Adding a New Customer

1. Create folder `profiles/<new-customer>/`
2. Add `connections.json` — use their prefix (e.g. `ACME_`) for env var names
3. Add `.env` — fill in their actual credentials
4. Add table configs under `profiles/<new-customer>/configs/`
5. Run: `python scripts/run_batch.py --profile <new-customer>`

No code changes required. The tool is fully customer-agnostic.

---

## Quick Reference

```bash
# Verify connections
python scripts/test_connections.py --profile kpn

# End-to-end smoke test
python scripts/setup_test_data.py --profile kpn
python scripts/run_comparison.py  --profile kpn --config kpn_comparison_test.json

# Single table
python scripts/run_comparison.py --profile kpn --config my_table.json

# All tables
python scripts/run_batch.py --profile kpn

# All tables — 4 parallel workers
python scripts/run_batch.py --profile kpn --workers 4

# Open dashboard (Windows)
start ui/index.html
```

---

## GitHub Actions — Automated Daily Run

The workflow at `.github/workflows/daily_compare.yml` runs automatically at **06:00 UTC every day**.

Add credentials as repository secrets: GitHub → Settings → Secrets and variables → Actions:

```
KPN_TERADATA_HOST    KPN_TERADATA_USER    KPN_TERADATA_PASS    KPN_TERADATA_DB
KPN_FABRIC_SERVER    KPN_FABRIC_DB        KPN_FABRIC_TENANT_ID
KPN_FABRIC_CLIENT_ID KPN_FABRIC_CLIENT_SECRET
```

Reports are saved as workflow artifacts (30-day retention) and a run summary is posted to the job log.

---

## Security

- All credentials are stored in `.env` files which are **gitignored** — never committed
- `connections.json` contains only variable names, never values
- For CI/CD, credentials go into GitHub repository secrets
- No credentials appear in any source file, config file, or log output
