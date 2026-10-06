# Architecture — KPN Data Bridge

## The Big Picture

```
Customer's Agent Machine
│
├── profiles/kpn/.env              ← credentials (never committed)
├── profiles/kpn/connections.json  ← which ODBC driver + which env vars
└── profiles/kpn/configs/          ← one JSON per table to compare
        │
        ▼
  scripts/run_batch.py --profile kpn
        │
        ├── connections/factory.py
        │     Reads connections.json → resolves env vars → builds ODBC string
        │
        ├── ODBC ──────────────────────────────────────────────┐
        │   Teradata ODS                          Fabric ODS   │
        │   (data loaded by PowerCenter)    (data loaded by IDMC)
        │                    │                        │
        │                    └──────────┬─────────────┘
        │                               ▼
        │                    comparators/ducky.py       ← Level 1
        │                    (DuckDB in-memory)
        │                    Row counts, PK match,
        │                    field mismatch counts
        │                               │
        │                    ┌──────────┴──────────┐
        │                  PASS ✅              FAIL ❌
        │                    │                    │
        │              JSON report       comparators/deepdiff.py  ← Level 2
        │                               Exact row-by-row diff
        │                               Left value vs Right value
        │                               Encoding issues
        │                                    │
        │                              Detailed JSON report
        │
        └── reports/aggregator.py
              Merges all table reports → group-report.json
                        │
                        ▼
                  ui/index.html
                  (open in browser — no server needed)
```

---

## What This Tool Does (and Does Not Do)

| | |
|---|---|
| Connects directly to Teradata ODS via ODBC | ✅ |
| Connects directly to Fabric ODS via ODBC | ✅ |
| Compares data loaded by PowerCenter vs IDMC | ✅ |
| Uses IDMC REST APIs at runtime | ❌ Not needed |
| Reads IDMC connection XMLs | ❌ Not needed |
| Any middleware or Data Access Layer | ❌ Not needed |

IDMC is the ETL pipeline that loaded data into Fabric. Once the data is there, this tool connects directly — IDMC plays no role at runtime.

---

## Folder Structure

```
kpn-data-bridge/
│
├── profiles/                        ← one folder per customer
│   ├── kpn/
│   │   ├── connections.json         ← connection names → driver type + env var names
│   │   ├── .env                     ← actual secrets (gitignored, never committed)
│   │   └── configs/                 ← one JSON config per table
│   │       ├── t_odsin_addr.json
│   │       └── t_odsin_contact.json
│   │
│   └── bmw/                         ← next customer, zero code change needed
│       ├── connections.json
│       ├── .env
│       └── configs/
│
├── connections/
│   └── factory.py                   ← only file that knows how to build ODBC strings
│
├── comparators/
│   ├── ducky.py                     ← Level 1: DuckDB fast comparison
│   └── deepdiff.py                  ← Level 2: exact row-by-row diff
│
├── reports/                         ← runtime output JSONs (gitignored)
│
├── ui/
│   └── index.html                   ← static dashboard, reads group-report.json
│
├── scripts/
│   ├── test_connections.py          ← smoke test: can we reach both DBs?
│   ├── run_comparison.py            ← run a single table comparison
│   └── run_batch.py                 ← run all tables for a profile
│
├── .github/
│   └── workflows/
│       └── daily_compare.yml        ← Phase 5: scheduled GitHub Actions run
│
├── .env.example                     ← documents all env var names, no real values
├── requirements.txt
├── ARCHITECTURE.md                  ← this file
└── CLAUDE.md
```

---

## How Credentials Flow (Never Hardcoded)

```
profiles/kpn/.env                    profiles/kpn/connections.json
─────────────────────────────        ──────────────────────────────────────
KPN_TERADATA_HOST=td.kpn.nl    →    "host_env": "KPN_TERADATA_HOST"
KPN_TERADATA_USER=svc_recon    →    "user_env": "KPN_TERADATA_USER"
KPN_TERADATA_PASS=secret       →    "pass_env": "KPN_TERADATA_PASS"
KPN_FABRIC_SERVER=ws.fabric... →    "server_env": "KPN_FABRIC_SERVER"
...                                           │
                                              ▼
                                  connections/factory.py
                                  reads var names from connections.json
                                  → reads actual values from .env
                                  → builds ODBC connection string
                                              │
                                              ▼
                                  comparators/ducky.py
                                  (only ever sees a connection object,
                                   never sees credentials directly)
```

### `profiles/kpn/connections.json`

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
    "type": "sqlserver",
    "server_env":   "KPN_FABRIC_SERVER",
    "database_env": "KPN_FABRIC_DB",
    "user_env":     "KPN_FABRIC_USER",
    "pass_env":     "KPN_FABRIC_PASS",
    "auth":         "ActiveDirectoryPassword"
  }
}
```

### `profiles/kpn/.env` (gitignored)

```
KPN_TERADATA_HOST=tdprod.kpn.internal
KPN_TERADATA_USER=svc_recon
KPN_TERADATA_PASS=your_password_here
KPN_TERADATA_DB=ODS_SCHEMA
KPN_FABRIC_SERVER=yourworkspace.datawarehouse.fabric.microsoft.com
KPN_FABRIC_DB=ODS_FABRIC
KPN_FABRIC_USER=your_user_here
KPN_FABRIC_PASS=your_password_here
```

---

## Table Config Format

Each table gets one JSON file in `profiles/<customer>/configs/`. It references connection names only — never credentials.

```json
{
  "type": "ducky",
  "meta": {
    "run_id": "KPN-2026-10-06",
    "deep_diff_on_fail": true,
    "deep_diff_engine": "deepdiff"
  },
  "left": {
    "connection": "odbc_teradata",
    "object": "ODS_SCHEMA.T_ODSIN_ADDR",
    "condition": "optional WHERE clause e.g. DELETED_FLAG IS NULL"
  },
  "right": {
    "connection": "odbc_fabric",
    "object": "ODS_FABRIC.T_ODSIN_ADDR",
    "condition": "optional WHERE clause"
  },
  "columns": {
    "pk":       ["ROW_ID"],
    "excluded": ["LOAD_DATE", "BATCH_ID", "ETL_HASH"]
  }
}
```

**Field notes:**
- `condition` — optional SQL WHERE predicate, appended when fetching rows (useful for filtering soft-deleted records)
- `excluded` — columns skipped during comparison (audit/ETL metadata that legitimately differs)
- `pk` — primary key column(s) used to join left and right rows for field-level comparison

---

## Two-Level Comparison Logic

### Level 1 — DuckDB (`comparators/ducky.py`)

Runs on every table. Fast — seconds per table.

```
Fetch left side via ODBC  ──┐
                             ├──► DuckDB in-memory ──► Compare ──► PASS / FAIL
Fetch right side via ODBC ──┘
```

Checks performed:
- Row count match (left total vs right total)
- PKs only in left (missing from target)
- PKs only in right (extra rows in target)
- Field-level mismatch count per column
- Encoding mismatch detection per column

Output JSON:
```json
{
  "status": "FAIL",
  "runtime": "2m 1s",
  "statistics": {
    "left_total": 6634247,
    "right_total": 6634247,
    "left_unique": 0,
    "right_unique": 0,
    "matched_pk_records": 6634232,
    "different_records": 15,
    "identical_records": 6634232
  },
  "field_results": {
    "ADDR": 14,
    "CITY": 1
  },
  "encoding_mismatches_by_field": {
    "CITY": 120,
    "ZIPCODE": 38,
    "ADDR": 261
  }
}
```

### Level 2 — Deep Diff (`comparators/deepdiff.py`)

Runs only on FAIL tables. Slower — minutes per table. Shows exact values.

```
FAIL table ──► bisect PK range ──► narrow to differing rows ──► per-record field diff
```

Extends the Level 1 report with:
```json
{
  "diff_records": [
    {
      "pk": { "ROW_ID": "12345" },
      "fields": {
        "ADDR": { "left": "Kühlstraße 17", "right": "Kuhlstra?e 17" },
        "CITY": { "left": "München",       "right": "Munchen" }
      }
    }
  ]
}
```

---

## CLI Usage

```bash
# Check that both database connections work
python scripts/test_connections.py --profile kpn

# Compare a single table
python scripts/run_comparison.py --profile kpn --config t_odsin_addr.json

# Run all tables for a customer
python scripts/run_batch.py --profile kpn

# Switch customer — zero code change
python scripts/run_batch.py --profile bmw
```

---

## HTML Dashboard

Open `ui/index.html` directly in a browser. It reads `reports/group-report.json` and renders:

```
┌─────────────────────────────────────────────────────────────────┐
│  Comparison Report Summary             Generated: 2026-10-06    │
│  Profile: kpn                          Run ID: KPN-2026-10-06   │
├────────────┬────────────┬────────────┬────────────────────────-─┤
│  REPORTS   │     OK     │    FAIL    │    ERROR                 │
│     12     │      9     │      3     │      0                   │
├────────────┴────────────┴────────────┴──────────────────────────┤
│ TABLE              STATUS  RUNTIME  LEFT       RIGHT    DIFF    │
│ T_ODSIN_ADDR       FAIL    2m 1s    6,634,247  6,634,247   15   │
│ T_ODSIN_CONTACT    OK      16.9s    1,228,631  1,228,631    0   │
│ ...                                                             │
└─────────────────────────────────────────────────────────────────┘
  Click a FAIL row to expand field-level and record-level diff
```

---

## Build Phases

| Phase | What Gets Built | Done When |
|---|---|---|
| **1** | Project skeleton, `connections/factory.py`, `.env.example`, `test_connections.py` | Both DB connections confirmed reachable |
| **2** | Config loader, `comparators/ducky.py`, `reports/writer.py`, `run_comparison.py` | One table → JSON report with PASS/FAIL |
| **3** | `comparators/deepdiff.py`, auto-triggered on FAIL | FAIL table shows exact field diffs |
| **4** | `run_batch.py`, `reports/aggregator.py`, `ui/index.html` | All tables visible in browser dashboard |
| **5** | `.github/workflows/daily_compare.yml` | Daily automated run with report artifacts |

---

## Key Technical Constraints

| Constraint | Detail |
|---|---|
| Teradata ODBC driver string | `DRIVER={Teradata}` with `DBCName=` (not `Server=`) |
| Fabric auth | `Authentication=ActiveDirectoryPassword` or service principal — plain user/pass may be blocked by tenant policy |
| DuckDB loading | Load full result sets as DataFrames — do not stream row-by-row in Level 1 |
| `realdiff` wheel | BMW's implementation uses a proprietary wheel. This project implements equivalent logic natively in `comparators/deepdiff.py` |
| Credentials | Always in `.env` files per profile, never in config JSONs or source code |
| Multi-customer | Switching customers = `--profile <name>`, zero code changes |
