"""
Apply src/db/schema.sql to the database.

Every statement in schema.sql is written to be idempotent — CREATE TABLE IF NOT
EXISTS, ALTER TABLE ... ADD COLUMN IF NOT EXISTS, CREATE INDEX IF NOT EXISTS —
so running this against a populated database adds what is missing and touches
nothing else. That is the point: schema changes arrived as hand-run SQL before
this existed, which is how the live schema and schema.sql drift apart.

Usage:
  python3 scripts/apply_schema.py --dry-run
  python3 scripts/apply_schema.py
"""

from __future__ import annotations

import argparse
from pathlib import Path

from src.db.connection import get_conn

SCHEMA_PATH = Path(__file__).resolve().parent.parent / "src" / "db" / "schema.sql"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true",
                        help="Print the statements without executing them")
    args = parser.parse_args()

    sql = SCHEMA_PATH.read_text(encoding="utf-8")

    if args.dry_run:
        print(f"Would execute {SCHEMA_PATH} ({len(sql)} chars) as one batch.")
        print(sql)
        return

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql)
    print(f"Applied {SCHEMA_PATH}.")


if __name__ == "__main__":
    main()
