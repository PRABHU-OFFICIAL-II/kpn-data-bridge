"""
Run all table comparisons for a profile and generate the group report.

Usage:
    python scripts/run_batch.py --profile kpn
    python scripts/run_batch.py --profile kpn --workers 4
    python scripts/run_batch.py --profile kpn --run-id KPN-2026-10-06
"""

import argparse
import json
import logging
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from comparators import config_loader
from comparators import ducky as ducky_comparator
from connections.factory import get_connection
from reports import aggregator as report_aggregator
from reports import writer as report_writer

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)


def _run_one(config_path: Path, profile_dir: Path) -> dict:
    """Runs a single table comparison. Returns the result dict."""
    try:
        cfg = config_loader.load(config_path)
    except (FileNotFoundError, ValueError) as e:
        return {"table": config_path.stem, "status": "ERROR", "error": str(e)}

    left_conn_name  = cfg["left"]["connection"]
    right_conn_name = cfg["right"]["connection"]

    try:
        left_conn  = get_connection(left_conn_name,  profile_dir)
        right_conn = get_connection(right_conn_name, profile_dir)
    except Exception as e:
        return {
            "run_id":  cfg["meta"]["run_id"],
            "left":    cfg["left"]["object"],
            "right":   cfg["right"]["object"],
            "status":  "ERROR",
            "runtime": "0s",
            "error":   str(e),
            "statistics": {},
            "field_results": {},
            "encoding_mismatches_by_field": {},
        }

    try:
        result = ducky_comparator.run(left_conn, right_conn, cfg)
    except Exception as e:
        result = {
            "run_id":  cfg["meta"]["run_id"],
            "left":    cfg["left"]["object"],
            "right":   cfg["right"]["object"],
            "status":  "ERROR",
            "runtime": "0s",
            "error":   str(e),
            "statistics": {},
            "field_results": {},
            "encoding_mismatches_by_field": {},
        }
    finally:
        left_conn.close()
        right_conn.close()

    report_writer.write(result, cfg)

    # Level 2 deep diff on FAIL
    if result["status"] == "FAIL" and cfg["meta"]["deep_diff_on_fail"]:
        _run_deep_diff(cfg, result, profile_dir)

    return result


def _run_deep_diff(cfg: dict, level1_result: dict, profile_dir: Path):
    from comparators import deepdiff as deep_comparator
    from reports import writer as rw
    import json as _json

    engine = cfg["meta"].get("deep_diff_engine", "deepdiff")
    if engine != "deepdiff":
        logger.warning("Deep diff engine '%s' not available — skipping Level 2.", engine)
        return

    try:
        left_conn  = get_connection(cfg["left"]["connection"],  profile_dir)
        right_conn = get_connection(cfg["right"]["connection"], profile_dir)
        deep_result = deep_comparator.run(left_conn, right_conn, cfg, level1_result)
        left_conn.close()
        right_conn.close()
    except Exception as e:
        logger.error("Deep diff failed for %s: %s", cfg["left"]["object"], e)
        return

    # Merge into the existing report file
    import re
    from datetime import timezone, datetime
    table_name = re.sub(r"[^a-zA-Z0-9_-]", "_", cfg["left"]["object"]).lower()
    run_id_safe = re.sub(r"[^a-zA-Z0-9_-]", "_", cfg["meta"]["run_id"]).lower()
    report_path = Path(__file__).parent.parent / "reports" / f"report_{table_name}_{run_id_safe}.json"

    if report_path.exists():
        with open(report_path, encoding="utf-8") as f:
            report = _json.load(f)
        report["deep_diff"] = deep_result
        with open(report_path, "w", encoding="utf-8") as f:
            _json.dump(report, f, indent=2, ensure_ascii=False)


def main():
    parser = argparse.ArgumentParser(description="Run all table comparisons for a profile")
    parser.add_argument("--profile",  required=True, help="Profile name (e.g. kpn)")
    parser.add_argument("--workers",  type=int, default=1,
                        help="Number of parallel workers (default: 1)")
    parser.add_argument("--run-id",   default=None,
                        help="Override run ID (default: taken from each config)")
    args = parser.parse_args()

    profile_dir  = Path(__file__).parent.parent / "profiles" / args.profile
    configs_dir  = profile_dir / "configs"

    if not profile_dir.exists():
        logger.error("Profile '%s' not found at %s", args.profile, profile_dir)
        sys.exit(1)

    config_files = sorted(configs_dir.glob("*.json"))
    if not config_files:
        logger.error("No config files found in %s", configs_dir)
        sys.exit(1)

    t_batch_start = time.time()

    logger.info("=" * 60)
    logger.info("Profile  : %s", args.profile)
    logger.info("Tables   : %d", len(config_files))
    logger.info("Workers  : %d", args.workers)
    logger.info("=" * 60)

    results = []

    if args.workers == 1:
        for i, cfg_path in enumerate(config_files, 1):
            logger.info("[%d/%d] %s", i, len(config_files), cfg_path.name)
            result = _run_one(cfg_path, profile_dir)
            results.append(result)
            logger.info("       → %s", result.get("status", "ERROR"))
    else:
        futures = {}
        with ThreadPoolExecutor(max_workers=args.workers) as pool:
            for cfg_path in config_files:
                future = pool.submit(_run_one, cfg_path, profile_dir)
                futures[future] = cfg_path.name

            for future in as_completed(futures):
                name = futures[future]
                try:
                    result = future.result()
                    results.append(result)
                    logger.info("✓ %s  → %s", name, result.get("status", "ERROR"))
                except Exception as e:
                    logger.error("✗ %s  → EXCEPTION: %s", name, e)

    # Determine run_id from results or override
    run_id = args.run_id or next(
        (r.get("run_id") for r in results if r.get("run_id")), "run-001"
    )

    # Aggregate and write group report + dashboard data
    try:
        group_path = report_aggregator.aggregate(args.profile, run_id)
    except Exception as e:
        logger.error("Aggregation failed: %s", e)
        group_path = None

    elapsed = time.time() - t_batch_start
    ok    = sum(1 for r in results if r.get("status") == "PASS")
    fail  = sum(1 for r in results if r.get("status") == "FAIL")
    error = sum(1 for r in results if r.get("status") == "ERROR")

    logger.info("=" * 60)
    logger.info("DONE  —  OK: %d  FAIL: %d  ERROR: %d  (%.1fs total)", ok, fail, error, elapsed)
    if group_path:
        logger.info("Group report : %s", group_path)
        logger.info("Dashboard    : open ui/index.html in your browser")
    logger.info("=" * 60)

    sys.exit(0 if error == 0 and fail == 0 else 1)


if __name__ == "__main__":
    main()
