"""
Smoke test — verifies that both source and target database connections work.

Usage:
    python scripts/test_connections.py --profile kpn
"""

import argparse
import io
import sys
from pathlib import Path

# Force UTF-8 output on Windows so status symbols print correctly
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

# Allow imports from project root
sys.path.insert(0, str(Path(__file__).parent.parent))

from connections.factory import get_connection


def test_connection(name: str, profile_dir: Path) -> bool:
    print(f"\n  Testing '{name}'...")
    try:
        conn = get_connection(name, profile_dir)
        cursor = conn.cursor()
        cursor.execute("SELECT 1")
        cursor.fetchone()
        conn.close()
        print(f"  ✅  '{name}' — connected successfully")
        return True
    except Exception as e:
        print(f"  ❌  '{name}' — FAILED")
        print(f"      {e}")
        return False


def main():
    parser = argparse.ArgumentParser(description="Test database connections for a profile")
    parser.add_argument("--profile", required=True, help="Profile name (e.g. kpn)")
    args = parser.parse_args()

    profile_dir = Path(__file__).parent.parent / "profiles" / args.profile

    if not profile_dir.exists():
        print(f"\n❌  Profile '{args.profile}' not found at {profile_dir}")
        sys.exit(1)

    import json
    connections_file = profile_dir / "connections.json"
    with open(connections_file) as f:
        connection_names = list(json.load(f).keys())

    print(f"\nProfile : {args.profile}")
    print(f"Location: {profile_dir}")
    print(f"Testing {len(connection_names)} connection(s): {', '.join(connection_names)}")
    print("-" * 50)

    results = [test_connection(name, profile_dir) for name in connection_names]

    print("\n" + "-" * 50)
    passed = sum(results)
    total = len(results)

    if passed == total:
        print(f"✅  All {total} connection(s) OK — ready to run comparisons.\n")
        sys.exit(0)
    else:
        print(f"❌  {total - passed} of {total} connection(s) failed.")
        print("    Update your credentials in profiles/{profile}/.env and retry.\n")
        sys.exit(1)


if __name__ == "__main__":
    main()
