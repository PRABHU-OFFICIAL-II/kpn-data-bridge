"""
Run a single table comparison end-to-end.

Usage:
    python scripts/run_comparison.py --profile kpn --config t_odsin_addr.json

The --config argument is resolved relative to profiles/<profile>/configs/.
You can also pass a full path.
"""

import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from comparators import config_loader
from comparators import ducky as ducky_comparator
from connections.factory import get_connection
from reports import writer as report_writer

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)


def main():
    parser = argparse.ArgumentParser(description="Run a single table comparison")
    parser.add_argument("--profile", required=True, help="Profile name (e.g. kpn)")
    parser.add_argument("--config",  required=True, help="Config filename or full path (e.g. t_odsin_addr.json)")
    args = parser.parse_args()

    profile_dir = Path(__file__).parent.parent / "profiles" / args.profile
    if not profile_dir.exists():
        logger.error("Profile '%s' not found at %s", args.profile, profile_dir)
        sys.exit(1)

    # Resolve config path — accept filename only or full path
    config_path = Path(args.config)
    if not config_path.is_absolute():
        config_path = profile_dir / "configs" / args.config
    if not config_path.suffix:
        config_path = config_path.with_suffix(".json")

    logger.info("=" * 60)
    logger.info("Profile : %s", args.profile)
    logger.info("Config  : %s", config_path.name)
    logger.info("=" * 60)

    # Load + validate config
    try:
        cfg = config_loader.load(config_path)
    except (FileNotFoundError, ValueError) as e:
        logger.error("Config error: %s", e)
        sys.exit(1)

    # Open connections
    left_conn_name  = cfg["left"]["connection"]
    right_conn_name = cfg["right"]["connection"]

    logger.info("Opening connection: %s", left_conn_name)
    try:
        left_conn = get_connection(left_conn_name, profile_dir)
    except Exception as e:
        logger.error("Cannot connect to left (%s): %s", left_conn_name, e)
        sys.exit(1)

    logger.info("Opening connection: %s", right_conn_name)
    try:
        right_conn = get_connection(right_conn_name, profile_dir)
    except Exception as e:
        logger.error("Cannot connect to right (%s): %s", right_conn_name, e)
        sys.exit(1)

    # Level 1 — DuckDB comparison
    logger.info("-" * 60)
    logger.info("LEVEL 1 — DuckDB comparison")
    logger.info("-" * 60)

    try:
        result = ducky_comparator.run(left_conn, right_conn, cfg)
    except Exception as e:
        logger.error("Comparison failed: %s", e)
        sys.exit(1)
    finally:
        try:
            left_conn.close()
        except Exception:
            pass
        try:
            right_conn.close()
        except Exception:
            pass

    # Write Level 1 report
    out_path = report_writer.write(result, cfg)
    logger.info("-" * 60)
    logger.info("Status  : %s", result["status"])
    logger.info("Runtime : %s", result["runtime"])
    logger.info("Report  : %s", out_path)

    # Level 2 — deep diff (only on FAIL)
    if result["status"] == "FAIL" and cfg["meta"]["deep_diff_on_fail"]:
        logger.info("-" * 60)
        logger.info("LEVEL 2 — Deep diff (table failed Level 1)")
        logger.info("-" * 60)
        _run_deep_diff(cfg, result, out_path, profile_dir)

    logger.info("=" * 60)
    sys.exit(0 if result["status"] == "PASS" else 1)


def _run_deep_diff(cfg: dict, level1_result: dict, level1_report: Path, profile_dir: Path):
    engine = cfg["meta"].get("deep_diff_engine", "deepdiff")

    if engine == "deepdiff":
        from comparators import deepdiff as deep_comparator

        left_conn_name  = cfg["left"]["connection"]
        right_conn_name = cfg["right"]["connection"]

        try:
            left_conn  = get_connection(left_conn_name,  profile_dir)
            right_conn = get_connection(right_conn_name, profile_dir)
            deep_result = deep_comparator.run(left_conn, right_conn, cfg, level1_result)
            left_conn.close()
            right_conn.close()
        except Exception as e:
            logger.error("Deep diff failed: %s", e)
            return

        # Merge deep diff results into the report
        import json
        with open(level1_report, encoding="utf-8") as f:
            report = json.load(f)

        report["deep_diff"] = deep_result

        from decimal import Decimal

        class _SafeEncoder(json.JSONEncoder):
            def default(self, o):
                if isinstance(o, Decimal):
                    return float(o)
                return super().default(o)

        with open(level1_report, "w", encoding="utf-8") as f:
            json.dump(report, f, indent=2, ensure_ascii=False, cls=_SafeEncoder)

        logger.info("Deep diff complete — %d differing records found",
                    len(deep_result.get("diff_records", [])))
        logger.info("Report updated: %s", level1_report)
    else:
        logger.warning("Deep diff engine '%s' is not available. Skipping Level 2.", engine)


if __name__ == "__main__":
    main()
