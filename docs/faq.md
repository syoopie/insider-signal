# FAQ

**Q: What's the difference between legal insider trading (what this tracks) and illegal insider trading?**

Illegal insider trading means buying or selling based on *material non-public information* — a secret merger, earnings that will miss estimates, a drug trial result that hasn't been announced. The insiders this system tracks are disclosing legal trades. They can legally trade based on their general business judgment about the company's direction, even if they know things the public doesn't about industry trends or strategic plans. The SEC requires these Form 4 disclosures specifically to create public transparency about those trades.

---

**Q: Won't the market immediately price in insider buys the moment the Form 4 is filed?**

For very high-profile cases it partially does, yes. But research shows that in aggregate, insider purchase signals continue to predict outperformance for 60–90 days after filing. The largest alpha is in small- and mid-cap stocks where fewer people are watching. This system focuses on those. A hedge fund seeing a CFO buy a $200K position in a $500M company isn't going to move the needle on their billion-dollar book — which leaves the signal intact for smaller participants.

---

**Q: What's the suggested holding period?**

Jeng, Metrick & Zeckhauser (2003) found the optimal window is **60–90 days**. The signal is a medium-term thesis: the insider bought because they have a specific view, and the market hasn't fully priced it in yet. It's not a short-term trade.

---

**Q: Why does the CFO score higher than the CEO?**

This is the most counterintuitive finding in the research (TipRanks/ResearchGate study). CFOs have the most complete real-time view of company finances — revenues, expenses, cash burn, and the gap between what's reported and what's real. CEOs spend more time on external relationships, strategy, and public positioning.

There's also a scrutiny effect: CEO trades attract immediate media and analyst attention, which may cause CEOs to trade more carefully (and thus less informationally) than CFOs who fly under the radar. See [scoring.md](scoring.md) for the full role breakdown.

---

**Q: Why only S&P 500 + Russell 2000?**

The free Neon database tier is 0.5 GB. There are ~2,000 Form 4 filings per day across all US public companies. Without a universe filter, the database fills up in a few months. The S&P 500 + Russell 2000 covers ~3,500 companies — all large/mid-caps and the most liquid small-caps where insider signals have been studied. The storage estimate with this filter is ~160 MB at steady state, well within the limit.

If you want to track a company outside these indexes, add its ticker to `data/tickers.txt`.

---

**Q: Can I track insider sales too?**

Sales are stored in the database (transaction code `S`) but not scored. Insiders sell for many reasons — diversification, taxes, estate planning, life expenses — that have nothing to do with their view of the company. The academic research on sale informativeness is weak and inconsistent compared to purchases. The current system focuses where the research is strongest.

---

**Q: How do I know the daily ingest is running?**

Three ways:
1. GitHub Actions tab in your repo — green checkmark or red X per run.
2. You'll receive a daily Telegram summary even on days with no signals.
3. If the ingest crashes, you get an immediate Telegram error message with the stack trace.

---

**Q: The dashboard takes ~30 seconds to load sometimes.**

Streamlit puts apps to sleep after ~12 hours of no visitors. The keep-alive workflow pings it twice a day to reduce how often this happens, but you may still hit a cold start occasionally. Subsequent page loads are fast once it's awake.

---

**Q: The backtest shows no data.**

The backtest needs signals that are at least 33 days old (30-day horizon + 3-day execution lag). If you just set the system up, there's nothing to backtest yet. The weekly Sunday job will start producing results once enough signals have aged out. Run a 365-day bootstrap to backfill enough historical data to see immediate results.

---

**Q: A ticker I care about isn't appearing in signals.**

Either: (a) it's not in `data/tickers.txt` — add it manually, or (b) there have been no open-market purchases by insiders of that company in the tracked window. The system only alerts on actual filings; it doesn't synthesize signals.

---

**Q: What does "routine trader" disqualified mean?**

An insider who buys in the same calendar month year after year (e.g. every May for 3 years) is classified as a routine trader. Research (Cohen, Malloy & Pomorski 2012) shows these mechanical trades have essentially zero predictive value. They're excluded before scoring so they don't dilute the signal quality. The disqualification requires 3 years of history per insider — it won't fire until enough historical data is loaded.

---

**Q: The bootstrap is taking a long time.**

`--days 730` takes 3–5 hours at 3 req/sec (intentionally slow to avoid EDGAR IP blocks during large bursts). Run it with `nohup` in the background and use `tail -f bootstrap.log` to watch progress. If you only want to start receiving today's signals, `--days 14` finishes in ~5 minutes and is enough for the system to work correctly.
