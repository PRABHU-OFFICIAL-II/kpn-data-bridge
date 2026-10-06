"""
Loads and validates a table comparison config JSON.
Returns a clean config dict with all defaults filled in.
"""

import json
from pathlib import Path


REQUIRED_TOP_LEVEL = ["type", "left", "right", "columns"]
REQUIRED_SIDE      = ["connection", "object"]
REQUIRED_COLUMNS   = ["pk"]


def load(config_path: Path) -> dict:
    if not config_path.exists():
        raise FileNotFoundError(f"Config file not found: {config_path}")

    with open(config_path) as f:
        cfg = json.load(f)

    _validate(cfg, config_path)
    _apply_defaults(cfg)
    return cfg


def _validate(cfg: dict, path: Path):
    for key in REQUIRED_TOP_LEVEL:
        if key not in cfg:
            raise ValueError(f"Config '{path}' is missing required field: '{key}'")

    for side in ("left", "right"):
        for key in REQUIRED_SIDE:
            if key not in cfg[side]:
                raise ValueError(f"Config '{path}': '{side}' is missing field: '{key}'")

    for key in REQUIRED_COLUMNS:
        if key not in cfg["columns"]:
            raise ValueError(f"Config '{path}': 'columns' is missing field: '{key}'")

    if not isinstance(cfg["columns"]["pk"], list) or len(cfg["columns"]["pk"]) == 0:
        raise ValueError(f"Config '{path}': 'columns.pk' must be a non-empty list")


def _apply_defaults(cfg: dict):
    cfg.setdefault("meta", {})
    cfg["meta"].setdefault("run_id", "run-001")
    cfg["meta"].setdefault("deep_diff_on_fail", False)
    cfg["meta"].setdefault("deep_diff_engine", "deepdiff")

    cfg["left"].setdefault("condition", None)
    cfg["right"].setdefault("condition", None)

    cfg["columns"].setdefault("excluded", [])

    cfg.setdefault("realdiff", {})
    cfg["realdiff"].setdefault("bisection_factor", 32)
    cfg["realdiff"].setdefault("bisection_threshold", 1000)
    cfg["realdiff"].setdefault("sample_size", 500)

    # Performance tuning — safe defaults for large tables
    cfg.setdefault("performance", {})
    perf = cfg["performance"]
    # Rows fetched per ODBC batch — tune down if memory is tight, up for speed
    perf.setdefault("chunk_size", 100_000)
    # Hard row cap — useful for smoke tests (null = no limit)
    perf.setdefault("row_limit", None)
    # Max differing PKs carried from Level 1 into Level 2 (caps deep diff scope)
    perf.setdefault("max_diff_pks", 10_000)
