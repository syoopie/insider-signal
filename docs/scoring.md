# Scoring System

Every open-market purchase (transaction code `P`) is scored 0–100. Scores of 70 and above
are surfaced on the dashboard; 90 and above, or a cluster that clears its bar, triggers a
Telegram alert.

**This describes the model that shipped on 2026-08-30.** Before that date the score was an
additive table of role, market cap, holdings increase and timing factors against a BUY
threshold of 60. That table was measured walk-forward and returned +0.78pp of selection
alpha at a permutation p of 0.27, which is a coin flip, so it was replaced rather than
retuned. [`findings.md`](findings.md) has the measurements, and
[`history/`](history/) the runs behind them.

---

## Hard Disqualifiers

These filters run before scoring. A disqualified filing scores 0 and is not stored as a
signal.

| Filter | Reason |
|---|---|
| **Not a purchase** | Sales, option exercises, RSU grants and awards are excluded. Only open-market buys (code `P`) are scored. |
| **10b5-1 plan flag** | Pre-arranged trading plan, set up months in advance rather than in response to current conditions. Cohen, Malloy & Pomorski (2012) find approximately zero alpha. |
| **Routine trader** | The insider bought in the same calendar month in ≥2 of the preceding 3 years. Requires 3 years of history and silently skips the check when the data does not span that far, so it never produces a false positive. |
| **Under $2,000** | Dividend reinvestment, fractional share plans and payroll contributions. |

Measured on the purchases they discard with `research/scripts/gates.py`, excluding routine
buyers is a measured zero on 124 months of the archive. The 10b5-1 rule cannot be read there
yet. Both stay on the literature; [findings.md](findings.md) has the numbers.

---

## The Score

One factor. **How far below its 52-week high the stock sat on the day the insider bought,
expressed as a percentile among the purchases disclosed in the preceding 30 days.**

`src/signals/discount.py` is the model. `src/market/context.py` fetches
`pct_below_52wk_high` once at ingest and stores it on the transaction row, which is what
lets the live path and `backfill_signals.py` read the same number. Below 120 reference
purchases the purchase is left unranked and scores 0. It does not fall back to a fixed
table: over 18 months the four picks that came from the fallback averaged −34.07pp
against the ranked picks' +15.59pp, so unrankable is the conservative answer.

| Condition | Score |
|---|---|
| At its 52-week high | 0 |
| The median recent purchase | 50 |
| Top 30% of recent purchases | 70, a WATCH |
| Top 10% of recent purchases | 90, a BUY |
| No 52-week high, under 200 bars of history | 0, never alerted |

**The reference has to be relative.** A fixed cutoff does not select a fixed fraction,
because the market moves every stock's discount together. The first version fired on 2.0% of
one month's purchases and 23.7% of another's, and scored +4.19pp mean with a −2.33pp median
against the trailing window's +9.92 and +5.77.

**The effect is a threshold, not a slope.** Within-month deciles 1 through 9 of the discount
are flat with negative medians; decile 10 alone returns +17.5% mean and +6.6% median. Rank
IC on the discount is −0.02. That is why no rank-transformed linear model built on it has
ever scored anything.

### Why there is only one factor

`score_breakdown` holds only the rank: `{"discount_rank": N}`, or `{"price_context_missing": 0}`
when there is no 52-week high. The buyer's role, whether the purchase was direct, how much it
added to the position and whether they had bought recently are recorded on each buyer in
`evidence.insiders[]`, because they describe the filing. They do not rank it.

Adding the old score back as a tiebreak inside the discount gate drops the result from
+11.13pp to +7.62pp. Tier-1 insider features inside the gate drop it to +6.80pp. Inside the
most discounted third, the number of cluster buyers points the wrong way at −4.53pp with
t=−1.85.

`cap_small`, weighted +15 for years on Lakonishok & Lee's small-cap result, measures with the
**opposite sign** on this data.

### What the Form 4 is actually doing

`research/scripts/insider_control.py` runs the same discount screen on stocks nobody bought, matched
on date, execution date and horizon.

| Top decile of discount | mean | median | hit rate |
|---|---|---|---|
| Insider purchases | +11.13pp | **+7.39pp** | **57.7%** |
| Placebo, same dates | +5.55pp | **−1.30pp** | 49.3% |

Half the mean is the discount alone. All of the median is the filing. **The Form 4 is the
gate and the discount is the ranker.**

---

## Cluster Signals

A cluster is 3 or more distinct insiders at the same issuer buying inside a 14-day rolling
window, counting only direct purchases of $25,000 or more that are not 10b5-1 trades, and
after removing identical-block and same-price-offering allocations. `src/signals/cluster.py`
holds the rule and `test_cluster.py` covers every filter.

The cluster flag changes classification; it adds no points.

---

## Signal Classification

| Condition | Signal type | Action |
|---|---|---|
| Cluster, average participant score ≥80, and either a tight cluster or a max score ≥85 | **CLUSTER_BUY** | Telegram alert |
| Cluster that misses that bar | **WATCH** | Dashboard only |
| Score ≥ 90 | **BUY** | Telegram alert |
| Score ≥ 70 | **WATCH** | Dashboard only |
| Score < 70 | **LOW** | Not stored as a signal |

The cluster bar uses the **average** of participant scores, not the maximum, so it asks
whether the group as a whole was buying weakness. Three insiders buying a stock at its
52-week high is a WATCH.

**A large-cap cluster is a WATCH.** `classify_signal()` applies it, and every signal is
classified there. The rule was set on a hit rate measured under the retired factor model.
Re-measured on 2026-09-13 it is below resolution: the database holds 9 large-cap cluster
purchases where the rule can bind, and the archive has no cap tiers. It stays until there is
data to decide it.

---

## What a Score Looks Like in Practice

The distribution is by construction close to uniform over 0–100, because the score is a
percentile. About a tenth of each month's purchases clear 90. Measured on 2026-09-08 across
7,633 eligible purchases, the deciles of the stored score against 90-day excess return:

| decile | score range | mean | median |
|---|---|---|---|
| 1 to 9 | 0 to 89 | −0.05% to +3.00% | −4.85% to −0.73% |
| 10 | 89 to 99 | **+15.49%** | **+2.29%** |

Nine flat deciles and a jump, which is the same shape the research found and the reason the
threshold sits where it does.
