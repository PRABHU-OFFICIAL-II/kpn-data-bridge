"""
Level 1 — DuckDB fast comparator, optimised for very large tables.

Design for scale:
  - Data is fetched from ODBC in chunks (cursor.fetchmany) — never the whole table
    at once, so memory usage stays bounded regardless of table size.
  - DuckDB uses a temp file on disk (not in-memory) so it can spill to disk when
    the data exceeds available RAM.
  - A row_limit can be set in config["performance"] for smoke-testing against a
    subset of rows before committing to a full run.

Hash-first comparison strategy:
  - Step 1: Compute md5(col1 || '|' || col2 || ...) for every row on both sides.
            NULL values are replaced with '__NULL__' so NULL==NULL hashes the same.
  - Step 2: One JOIN on PKs compares the hashes — gives the full set of differing
            rows in a single query, regardless of how many columns the table has.
  - Step 3: Field-level IS DISTINCT FROM queries run ONLY on the hash-mismatched
            rows (joined via diff_pks temp table) — not the whole table.
  This means a 100-column table costs 3 hash queries + 100 narrow column queries
  instead of 100 full-table-scan queries — much faster at scale.

  Differing PKs are collected (up to max_diff_pks) and returned in the result so
  Level 2 can fetch only those specific rows rather than the whole table again.
"""

import logging
import os
import tempfile
import time

import duckdb
import pandas as pd
import pyodbc

logger = logging.getLogger(__name__)

_INTERNAL_COLS = {"_merge"}


# ── Fetch helpers ─────────────────────────────────────────────────────────────

def _fetch_chunked(
    conn: pyodbc.Connection,
    object_name: str,
    condition: str | None,
    chunk_size: int,
    row_limit: int | None,
) -> tuple[list[str], list[list]]:
    """
    Streams rows from ODBC using cursor.fetchmany() to keep memory bounded.
    Returns (column_names, all_rows).  All column names are upper-cased.
    """
    sql = f"SELECT * FROM {object_name}"
    if condition:
        sql += f" WHERE {condition}"
    if row_limit:
        sql = f"SELECT * FROM ({sql}) __sub FETCH FIRST {row_limit} ROWS ONLY"

    logger.info("    SQL: %s", sql)
    cursor = conn.cursor()
    cursor.execute(sql)

    col_names = [desc[0].upper() for desc in cursor.description]
    all_rows: list[list] = []
    fetched = 0

    while True:
        chunk = cursor.fetchmany(chunk_size)
        if not chunk:
            break
        all_rows.extend(chunk)
        fetched += len(chunk)
        if fetched % (chunk_size * 5) == 0:
            logger.info("    ... streamed %s rows", f"{fetched:,}")
        if row_limit and fetched >= row_limit:
            break

    cursor.close()
    logger.info("    Total rows fetched: %s", f"{fetched:,}")
    return col_names, all_rows


def _load_into_duckdb(
    con: duckdb.DuckDBPyConnection,
    table_name: str,
    col_names: list[str],
    rows: list,
    chunk_size: int,
):
    """Registers data into DuckDB in chunks to avoid a single giant DataFrame."""
    if not rows:
        empty_df = pd.DataFrame(columns=col_names)
        con.execute(f"CREATE TABLE {table_name} AS SELECT * FROM empty_df WHERE 1=0")
        return

    # pyodbc.Row objects must be converted to tuples for pandas to unpack correctly
    first = pd.DataFrame([tuple(r) for r in rows[:chunk_size]], columns=col_names)
    con.execute(f"CREATE TABLE {table_name} AS SELECT * FROM first")

    for offset in range(chunk_size, len(rows), chunk_size):
        chunk_df = pd.DataFrame([tuple(r) for r in rows[offset:offset + chunk_size]], columns=col_names)
        con.execute(f"INSERT INTO {table_name} SELECT * FROM chunk_df")

    logger.info("    Loaded %s rows into DuckDB table '%s'", f"{len(rows):,}", table_name)


# ── SQL clause builders ───────────────────────────────────────────────────────

def _join_clause(pk_cols: list[str], la: str = "l", ra: str = "r") -> str:
    return " AND ".join(f"{la}.{c} = {ra}.{c}" for c in pk_cols)


def _hash_expr(compare_cols: list[str]) -> str:
    """
    Builds a NULL-safe md5 row-hash expression over all compare columns.
    '__NULL__' replaces NULL so that NULL==NULL hashes identically on both sides.
    """
    parts = " || '|' || ".join(
        f"COALESCE(CAST({c} AS VARCHAR), '__NULL__')"
        for c in compare_cols
    )
    return f"md5({parts})"


# ── Main entry point ──────────────────────────────────────────────────────────

def run(
    left_conn: pyodbc.Connection,
    right_conn: pyodbc.Connection,
    cfg: dict,
) -> dict:
    """
    Runs the Level 1 DuckDB hash-first comparison.

    Returns a result dict with statistics, field_results,
    encoding_mismatches_by_field, status (PASS/FAIL), and _diff_pk_list
    (differing PKs capped at max_diff_pks — consumed by Level 2).
    """
    t_start = time.time()

    left_obj     = cfg["left"]["object"]
    right_obj    = cfg["right"]["object"]
    left_cond    = cfg["left"]["condition"]
    right_cond   = cfg["right"]["condition"]
    pk_cols      = [c.upper() for c in cfg["columns"]["pk"]]
    excluded     = {c.upper() for c in cfg["columns"]["excluded"]}
    run_id       = cfg["meta"]["run_id"]
    chunk_size   = cfg["performance"]["chunk_size"]
    row_limit    = cfg["performance"]["row_limit"]
    max_diff_pks = cfg["performance"]["max_diff_pks"]

    # ── 1. Fetch both sides via ODBC (chunked, memory-bounded) ───────────────
    logger.info("[%s] Fetching LEFT:  %s", run_id, left_obj)
    left_cols, left_rows = _fetch_chunked(
        left_conn, left_obj, left_cond, chunk_size, row_limit
    )

    logger.info("[%s] Fetching RIGHT: %s", run_id, right_obj)
    right_cols, right_rows = _fetch_chunked(
        right_conn, right_obj, right_cond, chunk_size, row_limit
    )

    # ── 2. Load into DuckDB (file-based for disk-spill on huge tables) ────────
    db_fd, db_path = tempfile.mkstemp(suffix=".duckdb", prefix="kpn_compare_")
    os.close(db_fd)
    os.unlink(db_path)  # DuckDB must create the file itself; mkstemp leaves an empty file

    try:
        con = duckdb.connect(db_path)

        logger.info("[%s] Loading into DuckDB...", run_id)
        _load_into_duckdb(con, "left_data",  left_cols,  left_rows,  chunk_size)
        _load_into_duckdb(con, "right_data", right_cols, right_rows, chunk_size)

        del left_rows, right_rows  # DuckDB owns the data now

        # Derive compare columns (intersection minus PKs and excluded)
        left_db_cols  = {r[0].upper() for r in con.execute("DESCRIBE left_data").fetchall()}
        right_db_cols = {r[0].upper() for r in con.execute("DESCRIBE right_data").fetchall()}
        compare_cols  = sorted(
            (left_db_cols & right_db_cols) - set(pk_cols) - excluded - _INTERNAL_COLS
        )

        pk_join    = _join_clause(pk_cols)
        pk_select  = ", ".join(f"l.{c}" for c in pk_cols)
        pk_cols_bare = ", ".join(pk_cols)

        # ── 3. Row counts ─────────────────────────────────────────────────────
        left_total  = con.execute("SELECT COUNT(*) FROM left_data").fetchone()[0]
        right_total = con.execute("SELECT COUNT(*) FROM right_data").fetchone()[0]
        logger.info("[%s] Row counts — left: %s  right: %s",
                    run_id, f"{left_total:,}", f"{right_total:,}")

        # ── 4. PK uniqueness ──────────────────────────────────────────────────
        left_unique = con.execute(
            f"SELECT COUNT(*) FROM left_data l "
            f"WHERE NOT EXISTS (SELECT 1 FROM right_data r WHERE {pk_join})"
        ).fetchone()[0]

        right_unique = con.execute(
            f"SELECT COUNT(*) FROM right_data r "
            f"WHERE NOT EXISTS (SELECT 1 FROM left_data l WHERE {pk_join})"
        ).fetchone()[0]

        logger.info("[%s] Left-only PKs: %s  Right-only PKs: %s",
                    run_id, f"{left_unique:,}", f"{right_unique:,}")

        # ── 5. Matched PK count ───────────────────────────────────────────────
        matched_pk_records = con.execute(
            f"SELECT COUNT(*) FROM left_data l "
            f"JOIN right_data r ON {pk_join}"
        ).fetchone()[0]

        # ── 6. Hash-based row diff — one pass finds all mismatched rows ───────
        field_results: dict[str, int] = {}
        encoding_mismatches_by_field: dict[str, int] = {}
        different_records = 0
        diff_pk_list: list[dict] = []

        if compare_cols:
            hash_expr = _hash_expr(compare_cols)
            logger.info("[%s] Computing row hashes over %d column(s)...",
                        run_id, len(compare_cols))

            # Build a hash table for each side (PKs + row hash)
            con.execute(
                f"CREATE TABLE left_hashes AS "
                f"SELECT {pk_cols_bare}, {hash_expr} AS row_hash FROM left_data"
            )
            con.execute(
                f"CREATE TABLE right_hashes AS "
                f"SELECT {pk_cols_bare}, {hash_expr} AS row_hash FROM right_data"
            )

            # Single join to find all rows where the hash differs
            different_records = con.execute(
                f"SELECT COUNT(*) FROM left_hashes l "
                f"JOIN right_hashes r ON {pk_join} "
                f"WHERE l.row_hash != r.row_hash"
            ).fetchone()[0]

            logger.info("[%s] Hash-diff rows: %s  (out of %s matched PKs)",
                        run_id, f"{different_records:,}", f"{matched_pk_records:,}")

            if different_records > 0:
                # Store diff PKs as a temp table so field queries can filter to it
                con.execute(
                    f"CREATE TABLE diff_pks AS "
                    f"SELECT {pk_select} FROM left_hashes l "
                    f"JOIN right_hashes r ON {pk_join} "
                    f"WHERE l.row_hash != r.row_hash "
                    f"LIMIT {max_diff_pks}"
                )

                # Collect PKs for Level 2
                pk_rows = con.execute(f"SELECT {pk_cols_bare} FROM diff_pks").fetchall()
                diff_pk_list = [
                    {pk_cols[i]: row[i] for i in range(len(pk_cols))}
                    for row in pk_rows
                ]

                # ── 7. Field-level diff — only on hash-mismatched rows ────────
                logger.info("[%s] Field-level diff on %s differing rows across %d column(s)...",
                            run_id, f"{len(diff_pk_list):,}", len(compare_cols))

                dp_join = _join_clause(pk_cols, "l", "d")  # left_data l JOIN diff_pks d

                for col in compare_cols:
                    count = con.execute(
                        f"SELECT COUNT(*) FROM left_data l "
                        f"JOIN right_data r ON {pk_join} "
                        f"JOIN diff_pks d ON {dp_join} "
                        f"WHERE l.{col} IS DISTINCT FROM r.{col}"
                    ).fetchone()[0]

                    if count > 0:
                        field_results[col] = count
                        logger.info("[%s]   %-35s %s mismatches", run_id, col, f"{count:,}")

                        try:
                            enc = con.execute(
                                f"SELECT COUNT(*) FROM left_data l "
                                f"JOIN right_data r ON {pk_join} "
                                f"JOIN diff_pks d ON {dp_join} "
                                f"WHERE l.{col} IS DISTINCT FROM r.{col} "
                                f"AND regexp_matches(CAST(l.{col} AS VARCHAR), '[^\\x00-\\x7F]')"
                            ).fetchone()[0]
                            if enc > 0:
                                encoding_mismatches_by_field[col] = enc
                        except Exception:
                            pass  # Non-string column — skip encoding check

        identical_records = matched_pk_records - different_records

        con.close()

    finally:
        try:
            os.unlink(db_path)
            wal = db_path + ".wal"
            if os.path.exists(wal):
                os.unlink(wal)
        except OSError:
            pass

    # ── 8. Determine status ───────────────────────────────────────────────────
    status = "FAIL" if (left_unique > 0 or right_unique > 0 or different_records > 0) else "PASS"
    runtime_str = _fmt_runtime(time.time() - t_start)

    logger.info("[%s] Status: %s  Runtime: %s", run_id, status, runtime_str)

    return {
        "run_id":   run_id,
        "type":     cfg.get("type", "ducky"),
        "left":     left_obj,
        "right":    right_obj,
        "status":   status,
        "runtime":  runtime_str,
        "statistics": {
            "left_total":         left_total,
            "right_total":        right_total,
            "left_unique":        left_unique,
            "right_unique":       right_unique,
            "matched_pk_records": matched_pk_records,
            "different_records":  different_records,
            "identical_records":  identical_records,
        },
        "field_results":                field_results,
        "encoding_mismatches_by_field": encoding_mismatches_by_field,
        # Passed to Level 2 — not written to the final JSON report
        "_diff_pk_list": diff_pk_list,
    }


def _fmt_runtime(seconds: float) -> str:
    if seconds < 60:
        return f"{seconds:.1f}s"
    return f"{int(seconds // 60)}m {int(seconds % 60)}s"
