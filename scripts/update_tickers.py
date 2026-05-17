"""
Refreshes data/tickers.txt with current S&P 500 + Russell 2000 tickers.
Run quarterly: python scripts/update_tickers.py
"""

import sys
import os
import requests
import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

HEADERS = {"User-Agent": "InsiderSignal ticker-updater"}


def get_sp500_tickers() -> set:
    # Fetch with browser-like User-Agent (Wikipedia blocks urllib default)
    resp = requests.get(
        "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies",
        headers={"User-Agent": "Mozilla/5.0 (compatible; InsiderSignal/1.0)"},
        timeout=30,
    )
    from io import StringIO
    tables = pd.read_html(StringIO(resp.text))
    df = tables[0]
    col = next((c for c in df.columns if "symbol" in str(c).lower() or "ticker" in str(c).lower()), df.columns[0])
    tickers = set(df[col].str.upper().str.replace(".", "-", regex=False).dropna().tolist())
    print(f"  S&P 500: {len(tickers)} tickers")
    return tickers


def get_russell2000_tickers() -> set:
    # iShares IWM ETF holdings CSV
    url = (
        "https://www.ishares.com/us/products/239710/ishares-russell-2000-etf/"
        "1467271812596.ajax?fileType=csv&fileName=IWM_holdings&dataType=fund"
    )
    try:
        resp = requests.get(url, headers=HEADERS, timeout=60)
        lines = resp.text.splitlines()
        # Skip header rows (first ~9 lines are metadata)
        data_lines = [l for l in lines if l.count(",") >= 3]
        tickers = set()
        for line in data_lines[1:]:  # skip column header row
            parts = line.split(",")
            ticker = parts[0].strip().strip('"').upper()
            if ticker and ticker.isalpha() and 1 <= len(ticker) <= 5:
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
