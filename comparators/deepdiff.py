"""
Level 2 — Deep row-by-row comparator, optimised for large tables.

Design for scale:
  - Does NOT re-fetch the entire table. Instead it uses the list of differing
    PKs produced by Level 1 (stored in level1_result["_diff_pk_list"]) and
    fetches ONLY those specific rows from both sides — typically a tiny fraction
    of the full table, even for billion-row datasets.
  - PKs are sent to the database in batches (batch_size rows per IN-clause) to
    stay within ODBC/driver limits.
  - Left-only and right-only rows (missing PKs) are handled separately using the
    counts already computed by Level 1; a sample is fetched for display.
  - Output is capped at sample_size records to keep the JSON report readable.
"""

import logging
import time
from decimal import Decimal
from typing import Any

import pandas as pd
import pyodbc

logger = logging.getLogger(__name__)

# Max PKs per IN-clause batch — safe for Teradata and SQL Server
_IN_BATCH = 1_000


# ── Helpers ───────────────────────────────────────────────────────────────────

def _val(v: Any) -> Any:
    """Converts pandas NA / numpy scalars / Decimal to plain Python for JSON serialisation."""
    if isinstance(v, Decimal):
        return float(v)
    try:
        if pd.isna(v):
            return None
    except (TypeError, ValueError):
        pass
    if hasattr(v, "item"):
        return v.item()
    return v


def _fetch_by_pks(
    conn: pyodbc.Connection,
    object_name: str,
    condition: str | None,
    pk_cols: list[str],
    pk_values: list[dict],
    chunk_size: int,
) -> pd.DataFrame:
    """
    Fetches only the rows whose PKs are in pk_values.
    Sends PKs to the DB in batches of _IN_BATCH to avoid oversized IN-clauses.
    """
    if not pk_values:
        return pd.DataFrame()

    all_dfs: list[pd.DataFrame] = []

    for batch_start in range(0, len(pk_values), _IN_BATCH):
        batch = pk_values[batch_start: batch_start + _IN_BATCH]

        if len(pk_cols) == 1:
            col = pk_cols[0]
            placeholders = ", ".join("?" for _ in batch)
            params = [row[col] for row in batch]
            where = f"{col} IN ({placeholders})"
        else:
            # Composite PK: (col1 = ? AND col2 = ?) OR (col1 = ? AND col2 = ?) ...
            clauses = []
            params = []
            for row in batch:
                clause = " AND ".join(f"{c} = ?" for c in pk_cols)
                clauses.append(f"({clause})")
                params.extend(row[c] for c in pk_cols)
            where = " OR ".join(clauses)

        base_sql = f"SELECT * FROM {object_name}"
        filters = [where]
        if condition:
            filters.insert(0, condition)
        sql = base_sql + " WHERE " + " AND ".join(f"({f})" for f in filters)

        cursor = conn.cursor()
        cursor.execute(sql, params)
        col_names = [d[0].upper() for d in cursor.description]

        rows = []
        while True:
            chunk = cursor.fetchmany(chunk_size)
            if not chunk:
                break
            rows.extend(chunk)
        cursor.close()

        if rows:
            all_dfs.append(pd.DataFrame([tuple(r) for r in rows], columns=col_names))

    return pd.concat(all_dfs, ignore_index=True) if all_dfs else pd.DataFrame()


def _df_to_records(df: pd.DataFrame) -> list[dict]:
    """Converts a DataFrame to a list of plain Python dicts for JSON serialisation."""
    return [
        {col: _val(v) for col, v in row.items()}
        for _, row in df.iterrows()
    ]


# ── Main entry point ──────────────────────────────────────────────────────────

def run(
    left_conn: pyodbc.Connection,
    right_conn: pyodbc.Connection,
    cfg: dict,
    level1_result: dict,
) -> dict:
    """
    Runs Level 2 deep diff using the differing PKs identified by Level 1.

    Returns a dict with diff_records (exact left/right values per field),
    left_only / right_only samples, and summary counts.
    """
    t_start = time.time()

    left_obj    = cfg["left"]["object"]
    right_obj   = cfg["right"]["object"]
    left_cond   = cfg["left"]["condition"]
    right_cond  = cfg["right"]["condition"]
    pk_cols     = [c.upper() for c in cfg["columns"]["pk"]]
    excluded    = {c.upper() for c in cfg["columns"]["excluded"]}
    sample_size = cfg["realdiff"]["sample_size"]
    chunk_size  = cfg["performance"]["chunk_size"]
    run_id      = cfg["meta"]["run_id"]

    stats              = level1_result.get("statistics", {})
    diff_pk_list       = level1_result.get("_diff_pk_list", [])
    left_only_pk_list  = level1_result.get("_left_only_pk_list", [])
    right_only_pk_list = level1_result.get("_right_only_pk_list", [])

    logger.info("[%s] Deep diff — %s differing PKs to resolve", run_id, f"{len(diff_pk_list):,}")

    # ── A. Fetch only the differing rows from both sides ──────────────────────
    diff_pks_to_fetch = diff_pk_list[:sample_size]

    logger.info("[%s] Fetching %s differing rows from LEFT...", run_id, f"{len(diff_pks_to_fetch):,}")
    left_df = _fetch_by_pks(
        left_conn, left_obj, left_cond, pk_cols, diff_pks_to_fetch, chunk_size
    )

    logger.info("[%s] Fetching %s differing rows from RIGHT...", run_id, f"{len(diff_pks_to_fetch):,}")
    right_df = _fetch_by_pks(
        right_conn, right_obj, right_cond, pk_cols, diff_pks_to_fetch, chunk_size
    )

    # ── B. Build per-record field diffs ───────────────────────────────────────
    diff_records: list[dict] = []

    if not left_df.empty and not right_df.empty:
        left_df.columns  = [c.upper() for c in left_df.columns]
        right_df.columns = [c.upper() for c in right_df.columns]

        compare_cols = sorted(
            (set(left_df.columns) & set(right_df.columns)) - set(pk_cols) - excluded
        )

        merged = left_df.merge(right_df, on=pk_cols, suffixes=("__L", "__R"), how="inner")

        for _, row in merged.iterrows():
            fields: dict[str, dict] = {}
            for col in compare_cols:
                lv = _val(row.get(f"{col}__L"))
                rv = _val(row.get(f"{col}__R"))
                if lv != rv:
                    fields[col] = {"left": lv, "right": rv}

            if fields:
                diff_records.append({
                    "pk":     {c: _val(row[c]) for c in pk_cols},
                    "fields": fields,
                })

    # ── C. Fetch left-only / right-only rows using exact PKs from Level 1 ───────
    left_only_count  = stats.get("left_unique",  0)
    right_only_count = stats.get("right_unique", 0)

    left_only: list[dict] = []
    right_only: list[dict] = []

    if left_only_count > 0 and left_only_pk_list:
        pks_to_fetch = left_only_pk_list[:sample_size]
        logger.info("[%s] Fetching %s left-only rows (%s total)...",
                    run_id, f"{len(pks_to_fetch):,}", f"{left_only_count:,}")
        df = _fetch_by_pks(left_conn, left_obj, left_cond, pk_cols, pks_to_fetch, chunk_size)
        if not df.empty:
            df.columns = [c.upper() for c in df.columns]
            left_only = _df_to_records(df)

    if right_only_count > 0 and right_only_pk_list:
        pks_to_fetch = right_only_pk_list[:sample_size]
        logger.info("[%s] Fetching %s right-only rows (%s total)...",
                    run_id, f"{len(pks_to_fetch):,}", f"{right_only_count:,}")
        df = _fetch_by_pks(right_conn, right_obj, right_cond, pk_cols, pks_to_fetch, chunk_size)
        if not df.empty:
            df.columns = [c.upper() for c in df.columns]
            right_only = _df_to_records(df)

    runtime_str = _fmt_runtime(time.time() - t_start)
    logger.info("[%s] Deep diff complete — %s diff records resolved  runtime: %s",
                run_id, f"{len(diff_records):,}", runtime_str)

    return {
        "runtime":          runtime_str,
        "total_diff":       stats.get("different_records", len(diff_records)),
        "sample_size":      sample_size,
        "diff_records":     diff_records,
        "left_only_count":  left_only_count,
        "right_only_count": right_only_count,
        "left_only":        left_only,
        "right_only":       right_only,
    }


def _fmt_runtime(seconds: float) -> str:
    if seconds < 60:
        return f"{seconds:.1f}s"
    return f"{int(seconds // 60)}m {int(seconds % 60)}s"
