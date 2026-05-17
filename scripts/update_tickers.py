"""
Refreshes data/tickers.txt with current S&P 500 + Russell 2000 tickers.
Run quarterly: python scripts/update_tickers.py

Uses Wikipedia for S&P 500 (stable, well-maintained) and
iShares IWM ETF holdings for Russell 2000 (free CSV download).
"""

import sys
import os
import re
import requests

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

SP500_WIKI = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"
# iShares Russell 2000 ETF holdings (IWM) — free public CSV
IWM_HOLDINGS = "https://www.ishares.com/us/products/239710/ishares-russell-2000-etf/1467271812596.ajax?fileType=csv&fileName=IWM_holdings&dataType=fund"

HEADERS = {"User-Agent": "InsiderSignal ticker-updater"}


def get_sp500_tickers() -> set[str]:
    resp = requests.get(SP500_WIKI, headers=HEADERS, timeout=30)
    # Parse ticker symbols from the table
    tickers = set(re.findall(r'<td><b>([A-Z]{1,5})</b>', resp.text))
    if not tickers:
        # Fallback: look for /wiki/ticker pattern
        tickers = set(re.findall(r'title="([A-Z]{1,5})\s*\(', resp.text))
    print(f"  S&P 500: {len(tickers)} tickers")
    return tickers


def get_russell2000_tickers() -> set[str]:
    try:
        resp = requests.get(IWM_HOLDINGS, headers=HEADERS, timeout=60)
        tickers = set()
        lines = resp.text.splitlines()
        for line in lines[10:]:  # Skip header rows
            parts = line.split(",")
            if len(parts) >= 2:
                ticker = parts[0].strip().strip('"').upper()
                if re.match(r'^[A-Z]{1,5}$', ticker):
                    tickers.add(ticker)
        print(f"  Russell 2000: {len(tickers)} tickers")
        return tickers
    except Exception as e:
        print(f"  Russell 2000 fetch failed: {e} — skipping")
        return set()


def main():
    print("Fetching ticker universe...")
    sp500 = get_sp500_tickers()
    r2000 = get_russell2000_tickers()

    combined = sorted(sp500 | r2000)
    print(f"  Combined: {len(combined)} unique tickers")

    out_path = os.path.join(os.path.dirname(__file__), "..", "data", "tickers.txt")
    with open(out_path, "w") as f:
        f.write("\n".join(combined) + "\n")
    print(f"Written to {out_path}")


if __name__ == "__main__":
    main()
