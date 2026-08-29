#!/usr/bin/env python3
"""
Read-only data-quality audit across every table.

Every CHECKS row should return 0 except the "total rows" lines. A non-zero count
prints "<-- LOOK"; that is a prompt to investigate, not proof of a bug, since
some are expected (derivative-only filings carry no transactions, legacy rows
carry a NULL is_routine).

    uv run python scripts/audit_data.py
"""
from src.db.connection import get_cursor
from src.db.purchases import purchase_rollup

CHECKS = [
    # ---- companies ----------------------------------------------------------
    ("companies: total rows", "SELECT count(*) v FROM companies"),
    ("companies: NULL ticker", "SELECT count(*) v FROM companies WHERE ticker IS NULL OR ticker=''"),
    ("companies: NULL name", "SELECT count(*) v FROM companies WHERE name IS NULL OR name=''"),
    ("companies: duplicate tickers (distinct tickers w/ >1 CIK)",
     "SELECT count(*) v FROM (SELECT ticker FROM companies WHERE ticker IS NOT NULL GROUP BY ticker HAVING count(*)>1) t"),
    ("companies: cap_tier NULL", "SELECT count(*) v FROM companies WHERE cap_tier IS NULL"),
    ("companies: cap_tier outside known set",
     "SELECT count(*) v FROM companies WHERE cap_tier IS NOT NULL AND cap_tier NOT IN ('small','mid','large','unknown')"),
    ("companies: market_cap <= 0", "SELECT count(*) v FROM companies WHERE market_cap IS NOT NULL AND market_cap<=0"),
    ("companies: market_cap set but cap_tier='unknown'",
     "SELECT count(*) v FROM companies WHERE market_cap IS NOT NULL AND cap_tier='unknown'"),
    ("companies: cap_tier disagrees with market_cap",
     """SELECT count(*) v FROM companies WHERE market_cap IS NOT NULL AND (
          (market_cap <  2000000000 AND cap_tier<>'small') OR
          (market_cap >= 2000000000 AND market_cap < 10000000000 AND cap_tier<>'mid') OR
          (market_cap >=10000000000 AND cap_tier<>'large'))"""),
    ("companies: market_cap < $1M (implausible)",
     "SELECT count(*) v FROM companies WHERE market_cap IS NOT NULL AND market_cap < 1000000"),
    ("companies: market_cap > $5T (implausible)",
     "SELECT count(*) v FROM companies WHERE market_cap > 5000000000000"),

    # ---- form4_filings ------------------------------------------------------
    ("filings: total rows", "SELECT count(*) v FROM form4_filings"),
    ("filings: filed_date in the future", "SELECT count(*) v FROM form4_filings WHERE filed_date > CURRENT_DATE"),
    ("filings: period_date after filed_date",
     "SELECT count(*) v FROM form4_filings WHERE period_date IS NOT NULL AND period_date > filed_date"),
    ("filings: period_date > 1yr before filed_date (very late)",
     "SELECT count(*) v FROM form4_filings WHERE period_date IS NOT NULL AND filed_date - period_date > 365"),
    ("filings: NULL period_date", "SELECT count(*) v FROM form4_filings WHERE period_date IS NULL"),
    ("filings: cik not in companies",
     "SELECT count(*) v FROM form4_filings f WHERE cik IS NOT NULL AND NOT EXISTS (SELECT 1 FROM companies c WHERE c.cik=f.cik)"),
    ("filings: NULL cik", "SELECT count(*) v FROM form4_filings WHERE cik IS NULL"),
    ("filings: zero transactions",
     "SELECT count(*) v FROM form4_filings f WHERE NOT EXISTS (SELECT 1 FROM transactions t WHERE t.filing_id=f.id)"),

    # ---- transactions -------------------------------------------------------
    ("tx: total rows", "SELECT count(*) v FROM transactions"),
    ("tx: orphaned (no filing)",
     "SELECT count(*) v FROM transactions t WHERE NOT EXISTS (SELECT 1 FROM form4_filings f WHERE f.id=t.filing_id)"),
    ("tx: shares <= 0", "SELECT count(*) v FROM transactions WHERE shares IS NOT NULL AND shares<=0"),
    ("tx: price < 0", "SELECT count(*) v FROM transactions WHERE price_per_share IS NOT NULL AND price_per_share<0"),
    ("tx: NULL shares", "SELECT count(*) v FROM transactions WHERE shares IS NULL"),
    ("tx: NULL price", "SELECT count(*) v FROM transactions WHERE price_per_share IS NULL"),
    ("tx: total_value <> shares*price (>1% off)",
     """SELECT count(*) v FROM transactions WHERE shares IS NOT NULL AND price_per_share IS NOT NULL
        AND total_value IS NOT NULL AND price_per_share>0 AND shares>0
        AND abs(total_value - shares*price_per_share) > 0.01*abs(shares*price_per_share)"""),
    ("tx: P with price = 0 (free 'purchase')",
     "SELECT count(*) v FROM transactions WHERE transaction_code='P' AND price_per_share=0"),
    ("tx: P price > $10,000/share",
     "SELECT count(*) v FROM transactions WHERE transaction_code='P' AND price_per_share>10000"),
    ("tx: transaction_date in the future",
     "SELECT count(*) v FROM transactions WHERE transaction_date > CURRENT_DATE"),
    ("tx: transaction_date after its filing's filed_date",
     "SELECT count(*) v FROM transactions t JOIN form4_filings f ON f.id=t.filing_id WHERE t.transaction_date > f.filed_date"),
    ("tx: shares_after < shares bought (P only)",
     "SELECT count(*) v FROM transactions WHERE transaction_code='P' AND shares_after IS NOT NULL AND shares IS NOT NULL AND shares_after < shares"),
    ("tx: NULL role_category", "SELECT count(*) v FROM transactions WHERE role_category IS NULL"),
    ("tx: role_category outside known set",
     "SELECT count(*) v FROM transactions WHERE role_category IS NOT NULL AND role_category NOT IN ('cfo','ceo','coo','chairman','director','officer','other')"),
    ("tx: NULL insider_name", "SELECT count(*) v FROM transactions WHERE insider_name IS NULL OR insider_name=''"),
    ("tx: is_routine NULL (legacy rows)", "SELECT count(*) v FROM transactions WHERE is_routine IS NULL"),
    ("tx: exact duplicate rows within a filing",
     """SELECT count(*) v FROM (SELECT filing_id,insider_name,transaction_date,transaction_code,shares,price_per_share
        FROM transactions GROUP BY 1,2,3,4,5,6 HAVING count(*)>1) d"""),

    # ---- signals ------------------------------------------------------------
    ("signals: total rows", "SELECT count(*) v FROM signals"),
    ("signals: score outside 0-100", "SELECT count(*) v FROM signals WHERE score<0 OR score>100"),
    ("signals: signal_type outside known set",
     "SELECT count(*) v FROM signals WHERE signal_type NOT IN ('BUY','WATCH','CLUSTER_BUY','LOW')"),
    ("signals: signal_date in the future", "SELECT count(*) v FROM signals WHERE signal_date > CURRENT_DATE"),
    ("signals: BUY with score < 60", "SELECT count(*) v FROM signals WHERE signal_type='BUY' AND score<60"),
    ("signals: WATCH with score < 45 and no cluster",
     "SELECT count(*) v FROM signals WHERE signal_type='WATCH' AND score<45 AND cluster_flag=false"),
    ("signals: LOW with score >= 45", "SELECT count(*) v FROM signals WHERE signal_type='LOW' AND score>=45"),
    ("signals: CLUSTER_BUY without cluster_flag",
     "SELECT count(*) v FROM signals WHERE signal_type='CLUSTER_BUY' AND cluster_flag=false"),
    ("signals: cluster_flag but evidence says not a cluster",
     "SELECT count(*) v FROM signals WHERE cluster_flag=true AND (evidence->'cluster'->>'is_cluster')='false'"),
    ("signals: NULL evidence", "SELECT count(*) v FROM signals WHERE evidence IS NULL"),
    ("signals: NULL score_breakdown", "SELECT count(*) v FROM signals WHERE score_breakdown IS NULL"),
    ("signals: evidence missing filed_date", "SELECT count(*) v FROM signals WHERE evidence->>'filed_date' IS NULL"),
    ("signals: evidence has no insiders", "SELECT count(*) v FROM signals WHERE jsonb_array_length(COALESCE(evidence->'insiders','[]'::jsonb))=0"),
    # signal_date is the purchase date, so it legitimately differs from filed_date.
    # What must never happen is a signal with no filed_date, since the backtest
    # would then fall back to signal_date and enter before public disclosure.
    ("signals: no filed_date to key exec_date off (look-ahead risk)",
     "SELECT count(*) v FROM signals WHERE evidence->>'filed_date' IS NULL"),
    ("signals: ticker has no companies row",
     "SELECT count(*) v FROM signals s WHERE NOT EXISTS (SELECT 1 FROM companies c WHERE c.ticker=s.ticker)"),
    ("signals: large-cap CLUSTER_BUY (should be downgraded to WATCH)",
     """SELECT count(*) v FROM signals s WHERE s.signal_type='CLUSTER_BUY' AND EXISTS (
          SELECT 1 FROM companies c WHERE c.ticker=s.ticker AND c.cap_tier='large')"""),
    ("signals: alerted=true but type not BUY/CLUSTER_BUY",
     "SELECT count(*) v FROM signals WHERE alerted=true AND signal_type NOT IN ('BUY','CLUSTER_BUY')"),
    # A signal older than the last backfill window keeps whatever model scored it.
    # These rows carry factors the current scorer cannot emit, so they are not
    # comparable to the rest of the table and must not reach factor analysis.
    # Clear them by widening the window: backfill_signals.py --days 900 --force.
    ("signals: score_breakdown carries factors the current model cannot produce",
     """SELECT count(*) v FROM signals WHERE score_breakdown ?| ARRAY[
          'value_500k_plus','value_100k_plus','holdings_increase_30pct',
          'holdings_increase_15pct','fast_filing_0_1d','fast_filing_2d',
          'near_52wk_low_5pct','near_52wk_low_10pct',
          'cluster_size_4plus','cluster_size_5plus','cluster_size_6plus']"""),
    # Nothing in the weight table can sum above 61. A higher score is a row left
    # behind by an earlier model, not a stronger signal.
    ("signals: score above the current model's maximum of 61",
     "SELECT count(*) v FROM signals WHERE score > 61"),

    # ---- purchase rollup invariants (src/db/purchases.py) -------------------
    # The rollup totals same-day broker fills but must not total the repeats a
    # joint Form 4 emits, one per co-filer. Both look like several rows for one
    # insider on one day; only the fills have differing share counts or prices.
    # Getting this backwards multiplies a real purchase by the number of
    # co-filers, so these two invariants are worth checking on every audit.
    ("rollup: value exceeds the raw rows it came from (double-count)",
     f"""SELECT count(*) v FROM ({purchase_rollup()}) r
         JOIN (SELECT f.cik, t.insider_name, t.transaction_date, t.is_direct,
                      sum(t.total_value) raw_v
               FROM transactions t JOIN form4_filings f ON f.id=t.filing_id
               WHERE t.transaction_code='P'
               GROUP BY 1,2,3,4) raw
           ON raw.cik=r.cik AND raw.insider_name=r.insider_name
          AND raw.transaction_date=r.transaction_date AND raw.is_direct=r.is_direct
         WHERE r.total_value > raw.raw_v + 0.01"""),
    ("rollup: shares exceed the distinct fills they came from (joint-filing leak)",
     f"""SELECT count(*) v FROM ({purchase_rollup()}) r
         JOIN (SELECT f.cik, d.insider_name, d.transaction_date, d.is_direct,
                      sum(d.shares) distinct_shares
               FROM (SELECT DISTINCT t.filing_id, t.insider_name, t.transaction_date,
                            t.is_direct, t.shares, t.price_per_share
                     FROM transactions t WHERE t.transaction_code='P') d
               JOIN form4_filings f ON f.id=d.filing_id
               GROUP BY 1,2,3,4) raw
           ON raw.cik=r.cik AND raw.insider_name=r.insider_name
          AND raw.transaction_date=r.transaction_date AND raw.is_direct=r.is_direct
         WHERE r.shares > raw.distinct_shares + 0.01"""),

    # ---- backtest_runs ------------------------------------------------------
    ("backtest: total rows", "SELECT count(*) v FROM backtest_runs"),
    ("backtest: hit_rate outside 0-100",
     "SELECT count(*) v FROM backtest_runs WHERE hit_rate IS NOT NULL AND (hit_rate<0 OR hit_rate>100)"),
    ("backtest: n_trades = 0", "SELECT count(*) v FROM backtest_runs WHERE n_trades=0"),
    ("backtest: threshold not in (60,65)",
     "SELECT count(*) v FROM backtest_runs WHERE threshold NOT IN (60,65)"),
    ("backtest: horizon outside 30/60/90/180",
     "SELECT count(*) v FROM backtest_runs WHERE horizon_days NOT IN (30,60,90,180)"),
    ("backtest: NULL metrics", "SELECT count(*) v FROM backtest_runs WHERE metrics IS NULL"),
    ("backtest: run_date in the future", "SELECT count(*) v FROM backtest_runs WHERE run_date>CURRENT_DATE"),
]

CONTEXT = [
    ("transaction_code distribution",
     "SELECT transaction_code k, count(*) v FROM transactions GROUP BY 1 ORDER BY v DESC LIMIT 12"),
    ("signal_type distribution", "SELECT signal_type k, count(*) v FROM signals GROUP BY 1 ORDER BY v DESC"),
    ("cap_tier distribution", "SELECT COALESCE(cap_tier,'<NULL>') k, count(*) v FROM companies GROUP BY 1 ORDER BY v DESC"),
    ("role_category distribution",
     "SELECT COALESCE(role_category,'<NULL>') k, count(*) v FROM transactions GROUP BY 1 ORDER BY v DESC"),
    ("filings per month (last 14)",
     "SELECT to_char(filed_date,'YYYY-MM') k, count(*) v FROM form4_filings GROUP BY 1 ORDER BY 1 DESC LIMIT 14"),
    ("signals per month (last 14)",
     "SELECT to_char(signal_date,'YYYY-MM') k, count(*) v FROM signals GROUP BY 1 ORDER BY 1 DESC LIMIT 14"),
    ("latest backtest run_dates",
     "SELECT run_date::text k, count(*) v FROM backtest_runs GROUP BY 1 ORDER BY 1 DESC LIMIT 5"),
    ("data date range",
     """SELECT 'filings' k, (min(filed_date)::text || ' .. ' || max(filed_date)::text) v FROM form4_filings
        UNION ALL SELECT 'transactions', (min(transaction_date)::text||' .. '||max(transaction_date)::text) FROM transactions
        UNION ALL SELECT 'signals', (min(signal_date)::text||' .. '||max(signal_date)::text) FROM signals"""),
]


def main():
    with get_cursor() as cur:
        print("=" * 72)
        print("CHECKS  (0 = clean unless it is a 'total rows' line)")
        print("=" * 72)
        for label, sql in CHECKS:
            cur.execute(sql)
            v = cur.fetchone()["v"]
            flag = "" if (v == 0 or "total rows" in label) else "   <-- LOOK"
            print(f"{v:>10,}  {label}{flag}")

        print()
        print("=" * 72)
        print("CONTEXT")
        print("=" * 72)
        for label, sql in CONTEXT:
            print(f"\n-- {label}")
            cur.execute(sql)
            for r in cur.fetchall():
                vals = list(r.values())
                print(f"   {str(vals[0]):<28} {vals[1]}")


if __name__ == "__main__":
    main()
