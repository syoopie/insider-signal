# FAQ

**Q: What's the difference between the insider trading this tracks and illegal insider trading?**

Illegal insider trading means trading on *material non-public information*: a secret merger,
earnings that will miss, a drug trial result nobody has announced. The insiders this system
tracks are disclosing legal trades. They may trade on their general judgment about the company,
and the SEC requires the Form 4 precisely so those trades are public.

---

**Q: Won't the market price in an insider buy the moment the Form 4 is filed?**

Not in aggregate. Every entry here is made four days after the filing date, and the top decile of
purchases still returns well above its peers over the following 90 days. The effect does depend
on the price: a purchase in a stock near its 52-week high carries almost nothing, and one deep
below it carries almost all of the measured return. [findings.md](findings.md) has the numbers.

---

**Q: What's the suggested holding period?**

**90 days.** Measured over 124 months of purchases, the edge over other purchases peaks at 90
days on both significance and median. At 180 days the average keeps rising but the median turns
negative, because a handful of large winners carry it. Stop-losses were tested at every level
from −10% to −30% and made every number worse.

---

**Q: Does it matter whether the CFO, the CEO or a director bought?**

Not to the score. The old model weighted roles on a published study, and measured on this data
the ordering could not be recovered. The role is still shown on every signal, because it is useful
context, but it moves nothing. The same is true of company size and position size.

---

**Q: Why is a cluster of insiders shown as WATCH instead of CLUSTER_BUY?**

Three reasons, checked in this order. The buyers as a group were not buying into weakness: the
average of their scores is under 80. Or the cluster was spread out and nobody's score reached 85.
Or the company is a large-cap, which is always a WATCH. The number of buyers alone never promotes
a signal, because on this data it does not predict returns.

---

**Q: Why only the S&P 500 and Russell 2000?**

The free Neon tier is 0.5 GB and there are roughly 2,000 Form 4 filings a day across all US public
companies. Filtering to about 3,500 companies keeps 48 months of history near 200 MB.

To track a company outside these indexes, add its ticker to `data/tickers.txt`.

---

**Q: Can I track insider sales too?**

Sales are stored (transaction code `S`) but not scored. Insiders sell for taxes, diversification
and life expenses as often as for a view on the company. Net selling at a firm was tested as a
filter on purchases and measured as zero.

---

**Q: How do I know the daily ingest is running?**

1. The Actions tab in the repository shows a green check or a red cross per run.
2. A daily Telegram summary arrives even on days with no signals.
3. If the ingest crashes, a Telegram error message arrives with the stack trace.

---

**Q: The dashboard is slow on the first load sometimes.**

Neon scales the database to zero after a few idle minutes, and the first query has to wake it.
Everything after that is fast, and most pages serve from cache.

---

**Q: The backtest shows no data.**

The backtest needs signals at least 33 days old (a 30-day horizon plus the 3-day execution lag).
On a fresh setup there is nothing to measure until the Sunday job runs with old enough signals.
Bootstrap a year or more of history to see results immediately.

---

**Q: A ticker I care about isn't appearing in signals.**

One of these:

1. It is not in `data/tickers.txt`.
2. No insider made an eligible open-market purchase in the window.
3. The stock has less than a year of trading history, so it has no 52-week high to rank against
   and scores 0.

---

**Q: What does "routine trader" mean?**

An insider who buys in the same calendar month in at least two of the three previous years, such
as every May. Cohen, Malloy & Pomorski (2012) find those mechanical trades carry essentially no
information, so they are excluded before scoring. The check needs three years of history for that
insider, and it records "unknown" rather than guessing when the data does not reach that far.

---

**Q: The bootstrap is taking a long time.**

A two-year load fetches every Form 4 in the window from EDGAR, under its 10 requests a second
limit, and takes hours. Run it in the background with `nohup` and follow the log. `--days 14`
finishes in minutes and is enough for the daily job to work. Run `scripts/backfill_signals.py`
afterwards to build signals from what was loaded.
