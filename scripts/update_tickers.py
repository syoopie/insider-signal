"""
Refresh data/tickers.txt with the current S&P 500 + Russell 2000 universe.

Sources (both free, no API key):
  - S&P 500:     Wikipedia (stable, well-maintained list)
  - Russell 2000: Nasdaq screener — US stocks ranked 1001-3000 by market cap,
                  which matches FTSE Russell's annual reconstitution methodology

Run manually:  python scripts/update_tickers.py
Or via:        .github/workflows/quarterly_tickers.yml
"""

import os
import re
import sys
from io import StringIO

import requests
import pandas as pd

NASDAQ_HEADERS = {
    "User-Agent": "Mozilla/5.0",
    "Accept": "application/json, text/plain, */*",
    "Referer": "https://www.nasdaq.com/",
}
DATA_FILE = os.path.join(os.path.dirname(__file__), "..", "data", "tickers.txt")


def fetch_sp500() -> set:
    resp = requests.get(
        "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies",
        headers={"User-Agent": "Mozilla/5.0 (compatible; InsiderSignal/1.0)"},
        timeout=15,
    )
    resp.raise_for_status()
    tables = pd.read_html(StringIO(resp.text))
    df = tables[0]
    col = next(
        (c for c in df.columns if "symbol" in str(c).lower() or "ticker" in str(c).lower()),
        df.columns[0],
    )
    return set(df[col].str.upper().str.replace(".", "-", regex=False).dropna())


def fetch_russell2000() -> set:
    """
    Approximate Russell 2000 from Nasdaq screener: US stocks ranked 1001-3000
    by market cap (same slice FTSE Russell uses for the index).
    """
    resp = requests.get(
        "https://api.nasdaq.com/api/screener/stocks",
        params={"tableonly": "true", "limit": 25, "offset": 0, "download": "true"},
        headers=NASDAQ_HEADERS,
        timeout=20,
    )
    resp.raise_for_status()
    rows = resp.json()["data"]["rows"]

    clean = []
    for r in rows:
        sym = (r.get("symbol") or "").strip()
        country = (r.get("country") or "").strip()
        try:
            mc = float(r.get("marketCap") or 0)
        except (ValueError, TypeError):
            mc = 0
        if country == "United States" and re.match(r"^[A-Z]{1,5}$", sym) and mc > 0:
            clean.append((sym, mc))

    clean.sort(key=lambda x: x[1], reverse=True)
    return {sym for sym, _ in clean[1000:3000]}


def main():
    print("Fetching S&P 500 from Wikipedia...")
    try:
        sp500 = fetch_sp500()
        print(f"  {len(sp500)} tickers")
    except Exception as e:
        print(f"  FAILED: {e}", file=sys.stderr)
        sys.exit(1)

    print("Fetching Russell 2000 from Nasdaq screener...")
    try:
        russell = fetch_russell2000()
        print(f"  {len(russell)} tickers")
    except Exception as e:
        print(f"  FAILED: {e}", file=sys.stderr)
        sys.exit(1)

    combined = sorted(sp500 | russell)
    print(f"Combined universe: {len(combined)} tickers")

    with open(DATA_FILE, "w") as f:
        f.write("\n".join(combined) + "\n")
    print(f"Written to {DATA_FILE}")


if __name__ == "__main__":
    main()
