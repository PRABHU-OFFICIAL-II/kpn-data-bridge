"""
Creates the KPN_COMPARISON_TEST table on both Teradata and Fabric and loads
test data covering every comparison scenario the tool needs to handle.

Usage:
    python scripts/setup_test_data.py --profile kpn

Scenarios loaded:
  PersonID  1-5  : Clean matching rows              → PASS
  PersonID  6-7  : Encoding mismatches (ü,ö,ä,é,ë) → FAIL (encoding_mismatches_by_field)
  PersonID  8    : Exists in Teradata only           → FAIL (left_unique)
  PersonID  9    : Exists in Fabric only             → FAIL (right_unique)
  PersonID  10   : Field value diff (AMOUNT)         → FAIL (field_results)
  PersonID  11   : NULL vs empty string (EMAIL)      → FAIL (field_results)
  PersonID  12   : Trailing space (POSTAL_CODE)      → FAIL (field_results)
"""

import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from connections.factory import get_connection

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

TABLE_TERADATA = "c04939130.KPN_COMPARISON_TEST"
TABLE_FABRIC   = "dbo.KPN_COMPARISON_TEST"

# ── DDL ───────────────────────────────────────────────────────────────────────

DDL_TERADATA = """
CREATE TABLE c04939130.KPN_COMPARISON_TEST (
    PERSON_ID     INTEGER        NOT NULL,
    FULL_NAME     VARCHAR(100)   CHARACTER SET UNICODE,
    CITY          VARCHAR(50)    CHARACTER SET UNICODE,
    EMAIL         VARCHAR(100),
    POSTAL_CODE   VARCHAR(10),
    AMOUNT        DECIMAL(10,2),
    STATUS        CHAR(1)
)
PRIMARY INDEX (PERSON_ID)
"""

DDL_FABRIC = """
CREATE TABLE dbo.KPN_COMPARISON_TEST (
    PERSON_ID     INT             NOT NULL,
    FULL_NAME     VARCHAR(100),
    CITY          VARCHAR(50),
    EMAIL         VARCHAR(100),
    POSTAL_CODE   VARCHAR(10),
    AMOUNT        DECIMAL(10,2),
    STATUS        CHAR(1)
)
"""

# ── Test data ──────────────────────────────────────────────────────────────────
# Columns: PERSON_ID, FULL_NAME, CITY, EMAIL, POSTAL_CODE, AMOUNT, STATUS

# Rows inserted on BOTH sides — LEFT (Teradata) values
LEFT_ROWS = [
    # ── Scenario 1: Clean matching rows (PersonID 1-5) ────────────────────────
    # These should all PASS — identical on both sides
    (1,  "Jan de Vries",    "Amsterdam",  "jan.devries@kpn.nl",    "1012AB", 500.00,  "A"),
    (2,  "Maria Janssen",   "Rotterdam",  "m.janssen@kpn.nl",      "3011CD", 750.50,  "A"),
    (3,  "Pieter Bakker",   "Utrecht",    "p.bakker@kpn.nl",       "3511EF", 320.00,  "B"),
    (4,  "Sophie Visser",   "Eindhoven",  "s.visser@kpn.nl",       "5611GH", 980.75,  "A"),
    (5,  "Thomas Meijer",   "Tilburg",    "t.meijer@kpn.nl",       "5038IJ", 150.25,  "C"),

    # ── Scenario 2: Encoding mismatches (PersonID 6-7) ───────────────────────
    # LEFT has correct Unicode chars — RIGHT will have ASCII-degraded versions
    # Simulates what happens when Teradata Latin charset data loses encoding during migration
    (6,  "Jürgen Müller",   "München",    "j.muller@kpn.nl",       "8000DE", 640.00,  "A"),
    (7,  "Hélène Étienne",  "Liège",      "h.etienne@kpn.nl",      "4000BE", 430.00,  "B"),

    # ── Scenario 3: Left-only row (PersonID 8) ───────────────────────────────
    # EXISTS in Teradata, MISSING from Fabric → left_unique count goes up
    (8,  "Kees van der Berg", "Haarlem",  "k.vdberg@kpn.nl",       "2011KL", 210.00,  "A"),

    # ── Scenario 4: Field value diff — AMOUNT differs (PersonID 10) ──────────
    (10, "Anna de Groot",   "Groningen",  "a.degroot@kpn.nl",      "9711MN", 1250.50, "A"),

    # ── Scenario 5: NULL vs empty string — EMAIL is NULL on left (PersonID 11)
    (11, "Wim Smit",        "Breda",      None,                     "4811OP", 875.00,  "B"),

    # ── Scenario 6: Trailing space — POSTAL_CODE padded on left (PersonID 12)
    (12, "Lisa Boer",       "Arnhem",     "l.boer@kpn.nl",         "6811QR ", 330.00, "A"),
]

# Rows inserted on BOTH sides — RIGHT (Fabric) values
# PersonID must match LEFT except for the deliberate differences
RIGHT_ROWS = [
    # ── Scenario 1: Clean matching rows (same as left) ────────────────────────
    (1,  "Jan de Vries",    "Amsterdam",  "jan.devries@kpn.nl",    "1012AB", 500.00,  "A"),
    (2,  "Maria Janssen",   "Rotterdam",  "m.janssen@kpn.nl",      "3011CD", 750.50,  "A"),
    (3,  "Pieter Bakker",   "Utrecht",    "p.bakker@kpn.nl",       "3511EF", 320.00,  "B"),
    (4,  "Sophie Visser",   "Eindhoven",  "s.visser@kpn.nl",       "5611GH", 980.75,  "A"),
    (5,  "Thomas Meijer",   "Tilburg",    "t.meijer@kpn.nl",       "5038IJ", 150.25,  "C"),

    # ── Scenario 2: Encoding degraded (special chars stripped) ────────────────
    # ü→u, ö→o, ä→a, é→e, è→e, ë→e — classic Latin-1 → ASCII loss
    (6,  "Jurgen Muller",   "Munchen",    "j.muller@kpn.nl",       "8000DE", 640.00,  "A"),
    (7,  "Helene Etienne",  "Liege",      "h.etienne@kpn.nl",      "4000BE", 430.00,  "B"),

    # ── Scenario 3: Right-only row (PersonID 9) ──────────────────────────────
    # MISSING from Teradata, EXISTS in Fabric → right_unique count goes up
    (9,  "Extra Fabric Row", "Groningen", "extra@kpn.nl",           "9712PQ", 999.00,  "A"),

    # ── Scenario 4: AMOUNT is different from LEFT ─────────────────────────────
    (10, "Anna de Groot",   "Groningen",  "a.degroot@kpn.nl",      "9711MN", 1205.50, "A"),  # 1250.50 vs 1205.50

    # ── Scenario 5: EMAIL is empty string on right (NULL on left) ────────────
    (11, "Wim Smit",        "Breda",      "",                       "4811OP", 875.00,  "B"),

    # ── Scenario 6: POSTAL_CODE trimmed on right (trailing space on left) ────
    (12, "Lisa Boer",       "Arnhem",     "l.boer@kpn.nl",         "6811QR",  330.00, "A"),
]

INSERT_SQL = (
    "INSERT INTO {table} "
    "(PERSON_ID, FULL_NAME, CITY, EMAIL, POSTAL_CODE, AMOUNT, STATUS) "
    "VALUES (?, ?, ?, ?, ?, ?, ?)"
)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _drop_teradata(cursor, table: str):
    db, tbl = table.split(".")
    cursor.execute(
        f"SELECT COUNT(*) FROM DBC.TablesV "
        f"WHERE DatabaseName='{db}' AND TableName='{tbl}'"
    )
    if cursor.fetchone()[0] > 0:
        cursor.execute(f"DROP TABLE {table}")
        logger.info("  Dropped existing table: %s", table)


def _drop_fabric(cursor, table: str):
    cursor.execute(f"IF OBJECT_ID('{table}', 'U') IS NOT NULL DROP TABLE {table}")
    logger.info("  Dropped existing table (if existed): %s", table)


def _create_and_load(cursor, ddl: str, table: str, rows: list, side: str):
    logger.info("[%s] Creating table: %s", side, table)
    cursor.execute(ddl)
    logger.info("[%s] Inserting %d rows...", side, len(rows))
    sql = INSERT_SQL.format(table=table)
    for row in rows:
        cursor.execute(sql, row)
    logger.info("[%s] Done — %d rows inserted into %s", side, len(rows), table)


def _print_summary():
    logger.info("")
    logger.info("=" * 62)
    logger.info("TEST DATA SUMMARY")
    logger.info("=" * 62)
    scenarios = [
        ("1–5",  "Clean match",               "PASS"),
        ("6–7",  "Encoding mismatch (ü,é,ë)", "FAIL — encoding_mismatches_by_field"),
        ("8",    "Left-only (Teradata only)",  "FAIL — left_unique"),
        ("9",    "Right-only (Fabric only)",   "FAIL — right_unique"),
        ("10",   "AMOUNT field diff",          "FAIL — field_results.AMOUNT"),
        ("11",   "NULL vs empty string",       "FAIL — field_results.EMAIL"),
        ("12",   "Trailing space",             "FAIL — field_results.POSTAL_CODE"),
    ]
    for ids, scenario, expected in scenarios:
        logger.info("  PersonID %-5s | %-34s | %s", ids, scenario, expected)
    logger.info("=" * 62)
    logger.info("")
    logger.info("Next step — run the comparison:")
    logger.info("  python scripts/run_comparison.py --profile kpn --config kpn_comparison_test.json")
    logger.info("")


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Create and load test data on both sides")
    parser.add_argument("--profile", required=True, help="Profile name (e.g. kpn)")
    args = parser.parse_args()

    profile_dir = Path(__file__).parent.parent / "profiles" / args.profile

    # ── Teradata side ──────────────────────────────────────────────────────────
    logger.info("=" * 62)
    logger.info("Setting up Teradata side")
    logger.info("=" * 62)
    try:
        td_conn   = get_connection("odbc_teradata", profile_dir)
        td_cursor = td_conn.cursor()
        _drop_teradata(td_cursor, TABLE_TERADATA)
        _create_and_load(td_cursor, DDL_TERADATA, TABLE_TERADATA, LEFT_ROWS, "TERADATA")
        td_conn.close()
        logger.info("[TERADATA] Setup complete")
    except Exception as e:
        logger.error("[TERADATA] FAILED: %s", e)
        sys.exit(1)

    # ── Fabric side ────────────────────────────────────────────────────────────
    logger.info("")
    logger.info("=" * 62)
    logger.info("Setting up Fabric side")
    logger.info("=" * 62)
    try:
        fb_conn   = get_connection("odbc_fabric", profile_dir)
        fb_cursor = fb_conn.cursor()
        _drop_fabric(fb_cursor, TABLE_FABRIC)
        _create_and_load(fb_cursor, DDL_FABRIC, TABLE_FABRIC, RIGHT_ROWS, "FABRIC")
        fb_conn.close()
        logger.info("[FABRIC] Setup complete")
    except Exception as e:
        logger.error("[FABRIC] FAILED: %s", e)
        sys.exit(1)

    _print_summary()


if __name__ == "__main__":
    main()
