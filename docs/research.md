# Research References

What the system rests on, split by whether it is still load-bearing.

The distinction matters more than the citations do. **The eligibility rules rest on the
literature. The ranking rests on this system's own walk-forward test.** Several results below
were implemented as scoring weights, measured on our own data, and found to be
indistinguishable from zero or to have the wrong sign. They are kept here with what happened
to them, because deleting them would invite someone to add them back.

`docs/scoring-improvement-plan.md` section 7b is the measurement. `docs/beyond-price.md` is
the plan built on it.

---

## Still load-bearing

### Cohen, Malloy & Pomorski (2012)
*"Decoding Inside Information."* Journal of Finance, 67(3), 1009–1043.

**Finding:** opportunistic insider trades earn 82 basis points per month. Routine trades,
where an insider has a consistent seasonal pattern, earn approximately zero. The distinction
matters more than role or company size.

**Applied as** two hard disqualifiers, checked before scoring: the 10b5-1 plan flag, and the
routine-trader rule (bought the same calendar month in ≥2 of the preceding 3 years).

**Measured here, 2026-09-08.** Excluding 10b5-1 trades is worth +0.37pp against the months
those trades came from, at the 100th percentile of its null but q=0.28. Excluding routine
buyers scores **−0.19pp at the 0th percentile**, the wrong sign, also at q=0.28. Neither is
conclusive on 16 months. The routine rule is the first thing to re-test when the sample grows.

### Jeng, Metrick & Zeckhauser (2003)
*"Estimating the Returns to Insider Trading."* Review of Economics and Statistics, 85(2).

**Finding:** insider purchase portfolios earn roughly 6% annualised alpha. Optimal holding
horizon 60 to 90 days.

**Applied as** the reason only purchases are scored, and the 60 to 90 day hold shown in every
alert. The backtest's primary horizon is 90 days for the same reason.

---

## Measured here, and it holds

### Distance below the 52-week high

Not from the literature. It came out of this system's own walk-forward test and it is the
entire score.

Over 18 months and 6,690 out-of-sample rows at 90 days, top decile of each month, each pick
charged against its own volatility quintile inside that month: **+11.13pp, t=+2.29, median
+7.39pp, permutation p below 1 in 5,000.** It survives all four horizons, three selectivity
levels, both subperiods, one vote per ticker, a survivorship patch, a ticker-amputation
control against a matched null, and industry matching against the issuer's own sector fund.

The related published result is the microcap gradient-boosting preprint
([arXiv 2602.06198](https://arxiv.org/html/2602.06198)), which puts distance from the 52-week
high at 0.360 of total feature importance, more than four times the next feature. Treat that
figure as indicative: it is a preprint, and gain-based importance favours continuous features
over binary ones.

### The placebo control

`scripts/insider_control.py`. The same screen on stocks nobody bought, matched on date and
holding window, returns +5.55pp mean and a **−1.30pp median at a 49.3% hit rate**, against the
real purchases' +11.13 and +7.39 at 57.7%. Half the mean is the discount alone; all of the
median is the filing.

---

## Implemented, then measured away

Every entry here was once a scoring weight. All of them now score 0 points and remain in
`score_breakdown` only because the dashboard describes the filing with them.

### Lakonishok & Lee (2001)
*"Are Insider Trades Informative?"* Review of Financial Studies, 14(1), 79–111.

**Finding:** small-cap insider purchases generate +7.4% abnormal return over 12 months;
large-cap purchases show near-zero alpha.

**Was applied as** `cap_small` = +15.

**Measured here:** −4.75pp per standard deviation in the multivariate estimation, surviving
FDR at 5% with the **opposite sign to its weight**. Restricting the discount screen to small
caps drops it from +11.13pp to +6.08pp. The weight is now 0.

The buy-minus-sell half of this paper was never implemented at all, and was tested in 2026 as
`demand_buy_ratio`: nothing as a ranker, and +0.07pp against a 0.40pp resolution as a veto.

### TipRanks / ResearchGate role study

**Finding:** average annual returns when insiders buy their own stock, CFO 21.5%, Director
20.7%, Named Officer 19.8%, CEO 19.3%.

**Was applied as** the role weight table, CFO +20 down to Other +6.

**Measured here:** `role_director`, carrying the largest role weight at +16, estimates
−0.31pp per standard deviation and is indistinguishable from zero. The ordering is not
recoverable on this data. All role weights are now 0.

### Cluster studies

**Finding:** 3 or more insiders buying independently in a short window generates roughly 2×
the alpha of a single buy.

**Was applied as** a lower alert threshold for clusters.

**Measured here:** inside the most discounted third of purchases, the number of cluster buyers
points the **wrong way** at −4.53pp with t=−1.85. Cluster detection survives as a
classification rule with a raised bar (average participant score ≥80), not as a promotion.
Three insiders buying a stock at its 52-week high is a WATCH.

### Holdings-fraction studies

**Finding:** purchases expressed as a large percentage of the insider's existing position
predict positive abnormal returns.

**Was applied as** `holdings_increase_5pct` = +15.

**Measured here:** +0.61pp per standard deviation, indistinguishable from zero. Weight now 0.

### 52-week *low* proximity

**Finding:** insider buys near the 52-week low generate roughly 9.6% one-year abnormal
returns.

**Was applied as** +12 within 5% of the low and +7 within 10%.

**Deleted for a mechanical reason before it was ever measured properly.** Both tiers were
computable only in the live path, so the same purchase scored up to 12 points apart depending
on which entry point saw it, and both compared against *today's* low rather than the low as of
the trade. The correct version of this idea is the 52-week *high* distance now shipped, which
is stored at ingest so both paths read one number.

---

## Not implemented, and why

- **Filing lag as a penalty.** 12,247 of 13,294 purchases file within four days, so there is
  almost no variance to exploit. The rare tail was tested separately in 2026: excluding
  filings 30 or more days late is worth +0.03pp against a 0.20pp resolution.
- **Recent selling history as a penalty.** Tested in 2026 as `demand_buy_ratio`, above.
- **Ownership percentage.** Evidence is curvilinear; moderate ownership raises
  informativeness while very high ownership may reflect entrenchment.
- **Officer who also sits on the board.** Not computable from stored data. `parse_form4`
  reads `isDirector`, `isOfficer` and `isTenPercentOwner`, and `write_filing` stores none of
  them, so a CFO on the board and a CFO who is not are the same row.
  `scripts/build_form4_archive.py` keeps all three.
- **13F institutional ownership.** Quarterly and stale by up to 45 days, which cannot resolve
  a 90-day hold.
- **News and sentiment.** No free source with the coverage and point-in-time integrity this
  needs.

---

## Backtesting Methodology

Two rulers, and they answer different questions.

**`scripts/hillclimb.py` selects models.** Walk-forward with a rolling origin, refitting every
month on holds that had already closed. Each pick is judged against the other purchases of its
own month and its own volatility quintile, on the median as well as the mean, and the whole
fit is re-run under permuted labels so every candidate pays the same price in model search.
`scripts/gates.py` asks the same question about exclusions. Both print the minimum detectable
effect, because a null result without one cannot be distinguished from a null instrument.

**`scripts/run_backtest.py` reports the product.** Pooled excess return against SPY across the
whole lookback, which is what the dashboard shows. It is not a model-selection metric and must
not be used as one: it collapses 22 months of market conditions into one number that moves
with which months the model happened to fire in.

Controls that apply to both:

- **No look-ahead.** Entry keys off the filing date, never the transaction date. The stored
  `signal_date` *is* the transaction date and is a display axis only; the backtest derives
  `exec_date` from `evidence.filed_date` and deliberately ignores it.
- **Execution lag.** Entry at filing date + 1 + 3 calendar days.
- **Delisted stocks.** No prices for the window is charged as a −50% loss. A *failed request*
  is a different thing, is not charged −50%, and drops the signal from the sample.
- **Completed exits only.** Signals whose exit is still in the future are excluded and counted.
- **Coverage drift.** Any feature whose prevalence tracks how far back ingest reaches is
  dropped by `protocol.stable_features`. `first_purchase_12mo` never fires in one window and
  fires on 46% of the next, purely because of when ingest started.

Horizons: 30, 60, 90 and 180 days, with 90 as primary.
