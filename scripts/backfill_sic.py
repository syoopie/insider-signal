#!/usr/bin/env python3
"""
Fill companies.sic_code / companies.sic_description from EDGAR.

The Form 4 XML carries no industry classification, so nothing in the daily
ingest has ever populated sic_code. EDGAR's per-company submissions JSON does
carry it, one cheap request per CIK:

    https://data.sec.gov/submissions/CIK##########.json  ->  {"sic", "sicDescription"}

Additive and idempotent: only rows missing a SIC are touched unless --force is
given, so this can be re-run at any time and interrupted safely.

Usage:
    python3 scripts/backfill_sic.py                 # fill the gaps
    python3 scripts/backfill_sic.py --force         # re-fetch everything
    python3 scripts/backfill_sic.py --limit 200     # cap the work
"""
import argparse
import sys
import time
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.db.connection import get_conn
from src.ingest.common import log, phase

SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik}.json"
HEADERS = {
    "User-Agent": "InsiderSignal sunyupei19992@gmail.com",
    "Accept-Encoding": "gzip, deflate",
}

# EDGAR permits 10 requests/second; 6 leaves headroom and this is never urgent.
REQ_PER_SEC = 6.0
_last_call = 0.0


def _throttle() -> None:
    global _last_call
    gap = 1.0 / REQ_PER_SEC
    elapsed = time.monotonic() - _last_call
    if elapsed < gap:
        time.sleep(gap - elapsed)
    _last_call = time.monotonic()


def fetch_sic(cik: str) -> tuple[str | None, str | None]:
    """Returns (sic_code, sic_description); (None, None) when EDGAR has neither."""
    _throttle()
    try:
        resp = requests.get(
            SUBMISSIONS_URL.format(cik=str(cik).zfill(10)), headers=HEADERS, timeout=10
        )
        if resp.status_code != 200:
            return None, None
        data = resp.json()
        sic = (data.get("sic") or "").strip() or None
        desc = (data.get("sicDescription") or "").strip() or None
        return sic, desc
    except Exception as exc:  # noqa: BLE001 - a single failure must not stop the run
        log(f"  ! {cik}: {exc}")
        return None, None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true", help="re-fetch companies that already have a SIC")
    ap.add_argument("--limit", type=int, default=0, help="stop after this many companies")
    args = ap.parse_args()

    with get_conn() as conn:
        cur = conn.cursor()
        where = "" if args.force else "WHERE sic_code IS NULL OR sic_description IS NULL"
        limit = f"LIMIT {args.limit}" if args.limit > 0 else ""
        cur.execute(f"SELECT cik, ticker FROM companies {where} ORDER BY cik {limit}")
        targets = cur.fetchall()

        if not targets:
            log("Nothing to do — every company already has a SIC code.")
            return 0

        with phase(f"Fetching SIC for {len(targets)} companies"):
            filled = 0
            for i, (cik, ticker) in enumerate(targets, 1):
                sic, desc = fetch_sic(cik)
                if sic or desc:
                    cur.execute(
                        "UPDATE companies SET sic_code = COALESCE(%s, sic_code), "
                        "sic_description = COALESCE(%s, sic_description), updated_at = now() "
                        "WHERE cik = %s",
                        (sic, desc, cik),
                    )
                    filled += 1
                if i % 100 == 0:
                    conn.commit()
                    log(f"  {i}/{len(targets)} processed, {filled} filled")

            conn.commit()
            log(f"Done: {filled} of {len(targets)} companies now carry a SIC code.")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
