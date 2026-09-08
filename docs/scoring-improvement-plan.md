# Scoring Improvement Plan

Written 2026-08-29, against the clean post-audit baseline (backtest run_date 2026-08-29).

This plan argues that **the scoring model cannot be improved by changing weights or adding
factors until the measurement apparatus is rebuilt**, and lays out the rebuild, the
evaluation protocol, and the variables worth adding once those exist.

**Superseded for planning purposes by [`beyond-price.md`](beyond-price.md), 2026-09-05.**
This document remains the record of what was measured and is still the reference for how the
apparatus works. It is no longer the plan. Its unresolved question, why every insider-derived
variable measures zero while the price screen works, turns out to be a power problem: the
ruler in section 7b resolves about 5pp of selection alpha for an insider-family candidate, and
every one tested landed below 3.5pp. Read the successor before running another candidate.

Every number below was measured against the production database or live Yahoo Finance on
2026-08-29. Where something is inference rather than measurement it says so.

---

## 1. Summary

The current model is a four-factor conjunction wearing the costume of a 100-point additive
score, tuned across five rounds by a procedure that cannot distinguish signal from noise.

Three facts, each independently sufficient to block progress:

1. **The score has a theoretical maximum of 61 and the BUY threshold is 60.** A signal is a
   BUY if and only if all four positive factors fire. There is no ranking, no headroom, and
   no way for one factor to compensate for another.
2. **The negative class is thrown away.** 9,477 insider purchase-days produce 1,406 stored
   signals, because `signal_type == "LOW"` is discarded before the write. Of those, 347 are
   ever priced. We are fitting a model on 3.7% of the data, selected by the model itself.
3. **Factor weights are set by univariate lift computed on the sample that already passed the
   filter.** Conditioning on a sum induces negative correlation among its terms, so this
   procedure manufactures spurious negative lift for heavily-weighted factors. The tuning
   history is consistent with it having measured mostly that artifact.

Fixing measurement is worth more than any new variable. The plan is therefore ordered:
substrate first, protocol second, model form third, new variables fourth.

---

## 2. Evidence

### 2.1 The score is degenerate

Maximum attainable score from the current weight table:

```
role_director            +16   (best role)
cap_small                +15   (best cap tier)
holdings_increase_5pct   +15
prior_purchase_31_365d   +15   (best timing factor)
                        ----
                          61
```

Observed distribution over all 1,406 stored signals:

| score | signals | of which BUY |
|---|---|---|
| 61 | 222 | 202 |
| 60 | 51 | 44 |
| 46 | 594 | 0 |
| 45 | 133 | 0 |
| everything else | 406 | 2 |

246 of 248 BUY signals sit at 60 or 61. Two values (46 and 61) hold 58% of the population.
The four scores above 61 are stale rows from a May 2026 scoring round, discussed in 2.5.

`min(score, 100)` in `score_transaction` has never been reached and cannot be.

**Consequence.** "Score ≥ 60" is the boolean `director-or-cfo AND small-cap AND holdings-up-5%
AND prior-purchase`. Every weight change of more than one point relocates a large block of
signals across the threshold at once, which is why successive tuning rounds swung
`first_purchase_12mo` from +10 to −10 and `role_ceo` from +10 to −5 on a 61-point scale.

### 2.2 The four load-bearing factors barely discriminate

Fire rate across the 1,406 stored signals:

| factor | fires | rate | weight |
|---|---|---|---|
| `cap_small` | 1,078 | 77% | +15 |
| `role_director` | 1,070 | 76% | +16 |
| `holdings_increase_5pct` | 1,043 | 74% | +15 |
| `prior_purchase_31_365d` | 797 | 57% | +15 |

A factor present in three quarters of the population carries almost no information about
which member of that population to pick. These four are the entire model.

The rates are themselves inflated by selection: they are conditional on a signal having been
stored, and storage requires clearing WATCH, which these factors are what produce.

Meanwhile the cells that recent weight changes were based on:
`role_chairman` n=2, `role_ceo` n=18, `cap_mid` n=19, `role_officer` n=72.

### 2.3 The negative class is discarded

`scripts/backfill_signals.py:403` — `if signal_type == "LOW": continue`.

| stage | count |
|---|---|
| P transaction rows, last 730d | 13,294 |
| eligible (not 10b5-1) | 11,938 |
| distinct insider purchase-days | **9,477** across 1,314 issuers |
| stored signals (all types) | **1,406** — 1,021 WATCH, 248 BUY, 137 CLUSTER_BUY |
| priced by the backtest, 30d horizon | **347** |
| priced by the backtest, 180d horizon | 253 |

Two separate losses. LOW is dropped entirely, and signals are keyed `(ticker, signal_date)`,
so five insiders buying the same company on the same day collapse into one row carrying
**only the highest-scoring insider's breakdown** (`backfill_signals.py:237-239`). The other
four contribute nothing to any subsequent analysis, and the return gets attributed to one
person's factors.

You cannot estimate whether a factor predicts return from a sample that only contains
observations the factor helped select.

### 2.4 Returns are not dividend-adjusted

`src/market/prices.py:239` reads `indicators.quote[0].close`. Yahoo's chart API also exposes
`indicators.adjclose[0].adjclose`. Measured on 2026-08-29:

| symbol | window | raw close return | adjusted return | gap |
|---|---|---|---|---|
| NVDA (10:1 split) | 2024-05-20 → 2024-06-20 | +43.05% | +43.06% | 0.01pp |
| SPY | 2024 full year | +24.45% | +26.05% | **1.60pp** |
| T (high yield) | 2024 full year | +31.07% | +39.19% | **8.12pp** |

Splits are already handled — the NVDA test rules out the catastrophic failure mode. Dividends
are not.

This is not a wash against the SPY benchmark, because the error is proportional to
(ticker yield − SPY yield) × horizon. Insider buying works best in small-cap value names,
which yield more than SPY. **We are systematically understating the excess return of exactly
the signals the system is built to find**, in proportion to holding period, and any factor
correlated with dividend yield has its lift mismeasured. One-line fix, material effect.

### 2.5 The weight-setting tool has its own defects

`scripts/analyze_factors.py` is what produced every weight in the table. Three problems:

- **Fuzzy join.** The backtest's `detail` rows carry no signal id and no signal date
  (`engine.py:262-271`), so the analysis matches a return to a signal by searching for
  `abs((exec_date − signal_date).days − 4) < 8` (`analyze_factors.py:100-111`). For a ticker
  with several signals in a fortnight this can attribute a return to the wrong one.
- **Stale rows in scope.** Its signal query has no date filter. 204 signals dated 2024-05-16
  to 2024-07-29 predate the 730-day backfill window and still carry breakdowns from the May
  2026 model — 156 of them reference factors that no longer exist (`value_500k_plus`,
  `holdings_increase_30pct`, `fast_filing_0_1d`, `near_52wk_low_*`). *Measured:* zero of
  these fall inside the current backtest window, and the nearest is more than 30 days from
  the earliest `exec_date`, so they are not contaminating today's output. It is a live
  landmine, not a live wound.
- **Univariate lift.** `avg(return | factor present) − avg(return | factor absent)` ignores
  every other factor. `cap_small` and `role_director` co-occur heavily; shared variance is
  credited to both.

Combined with a filtered sample, univariate lift is not a weak estimator of factor value.
It is a biased one, and the bias has the wrong sign for the factors that matter most.

### 2.6 No holdout, and the data is already burned

Five tuning rounds, roughly 27 candidate factors, one 730-day sample, no train/test split, no
standard errors, no multiple-comparison control. The 730-day window is also a single market
regime, and overlapping 180-day holding windows mean the 253 observations at that horizon
contain far fewer than 253 independent draws.

CLAUDE.md already carries the caveat that round 4/5 weights were derived while the
`transaction_date` type bug was active. That is correct and it is the smaller problem.

### 2.7 What is *not* wrong

Worth stating so effort does not go here:

- The ingest and parser are correct as of the 2026-08-29 audit.
- `purchase_rollup` is the right grain for a purchase and is shared by all three call sites.
- Cluster detection filters (identical-block, same-price-offering, direct-only, $25k floor)
  are sound and tested.
- Splits are handled.
- The `filed_date` windowing and the `exec_date = filed_date + 4` convention are
  point-in-time correct. No look-ahead.
- Signal dating is fine and documented.

---

## 3. What was built

Phases 1 through 9 all ran between 2026-08-29 and 2026-08-30. **The plan text that used to
occupy this section has been cut: every item shipped or was consciously dropped, and the
record below is what remains useful.** Sections 7a and 7b are the findings, and they are
what the rest of the repo cites.

| # | Work | Outcome |
|---|---|---|
| 1 | Adjusted closes; `signal_id` in backtest detail; purge stale signals | Done. Dividend adjustment moved the backtest +0.02 / +0.13 / +0.28 / +0.62pp at 30/60/90/180d |
| 2 | Price panel, `build_research_dataset.py` | Done. 7,576 labelled purchases at 90d; the existing backtest reproduced to 0.003pp |
| 3 | Score the negative class | Done, and with no new table. The backfill's scoring loop moved to `src/signals/batch.py` and the research builder calls it, so there is one definition and no second writer. 9,336 scored, 99.08% parity with the pipeline |
| 4 | Evaluation protocol as a committed script | Done. Splits 2,925 / 1,536 / 762 with purge and embargo. **Superseded by `src/research/walkforward.py`; see 7b for why a single split was not enough** |
| 5 | Tier 1 features, multivariate estimation | Done. 6 of 38 candidates survived FDR 5%, two of them the same finding at r=+0.92 |
| 6 | Tier 2 features | Done in the research dataset only. The `price_context` columns on `transactions` were deliberately not added, because no model shipped that read them |
| 7 | Fit A / B / C, select on validation | Done. B selected at +23.83% against the score's +5.13%. C was never fitted, sample too small. A stability guard was added mid-phase |
| 8 | Single test-set evaluation | Done. Failed 1 of 6 pre-registered bars. **Null result** |
| 9 | Ship | Nothing shipped that changed a score. Five corrections and the whole research apparatus landed |

Phases 1 to 4 changed no scores. Anything from phase 6 onward triggers the golden rule in
`CLAUDE.md`.

## 7a. Outcomes, 2026-08-30

All nine phases ran. **No scoring change shipped, and that is the finding.**

### What the negative class showed (Phase 3)

Scoring every purchase instead of only the ones the model selects turned 331
priced signals into 8,306 labelled ones at 90 days. On that sample the score's
deciles are flat and non-monotone: the bottom decile (scores −30 to 0) returns
+3.90% mean and +1.25% median excess, the top decile (46–61) returns about +5%
mean and a *negative* median. Deciles 5 and 6 beat deciles 7, 8 and 9. Section
2.1 argued from the weight table that the score was a four-factor conjunction
rather than a ranking. This measures it.

### What the factor estimates showed (Phase 5)

Multivariate, clustered on ticker, Benjamini-Hochberg across 38 candidates, on
the training split (n=3,655 over 819 tickers):

| feature | beta per sd | verdict |
|---|---|---|
| net insider demand (`demand_buy_ratio`) | +6.08pp | survives FDR 5% |
| `demand_net_dollars` | −6.09pp | same finding, r = +0.92 with the above |
| `tx_pct_above_52wk_low` | +5.83pp | survives |
| `tx_ret_21d` | −5.52pp | survives, short-term reversal |
| `f_cap_small` | −4.75pp | survives, **opposite sign to its +15 weight** |
| `f_role_director` (+16) | −0.31pp | indistinguishable from zero |
| `f_holdings_increase_5pct` (+15) | +0.61pp | indistinguishable from zero |
| `f_prior_purchase_31_365d` (+15) | −0.22pp | indistinguishable from zero |

Three of the four load-bearing factors have no measurable effect and the fourth
has the wrong sign, on the split where the model should look its best.

### The stability guard the plan did not anticipate (Phase 7)

The first fit returned +32% mean excess against a +10% baseline, on weights of
+20.9 and +19.2 for timing factors. That was not a model, it was a clock.
`f_first_purchase_unverifiable` fires on **62.2% of training entries and 2.4% of
validation ones**; `f_first_purchase_12mo` **never fires in training and fires on
45.9% of validation**. Both rates are functions of how far back the database
reaches on a given date. CLAUDE.md already records the same failure in the
original factor, firing on 87% of pre-2025-04 signals against 32% after.

`protocol.stable_features` now drops any candidate whose mean moves more than
half a training standard deviation across a split boundary. Five go: both
first-purchase factors, `tx_ret_63d`, `track_n_prior` and `filing_lag_days`.
**This guard belongs in the plan permanently.** Any feature derived from what the
database can see drifts as coverage accumulates, and no amount of correct
cross-validation saves a model fitted on one coverage regime and deployed into
another.

### The pre-registered test evaluation (Phase 8)

Model B, ridge logistic at alpha=100 on the 33 stable candidates, selected on
validation where it returned +23.83% mean at top 153 against the current score's
+5.13%. On the test split, entries 2026-04-03 to 2026-06-01, top 76 of 762:

| | mean | median | hit | t | decile spread mean / median |
|---|---|---|---|---|---|
| Model B | +16.62% | +9.38% | 73.7% | +3.40 | +4.49pp / **−0.59pp** |
| current score | +12.83% | +9.71% | 75.0% | +3.77 | +4.24pp / +2.68pp |
| all eligible | +8.26% | +6.24% | 65.0% | +5.12 | — |
| small-cap only | +10.40% | +8.31% | 69.6% | +4.25 | — |
| random, 76 | +15.78% | +12.89% | 78.9% | +5.29 | — |

Six pre-registered bars, five passed. It fails **ranks on the median**, which is
the one the whole exercise exists to fix. It also loses to the current score on
median and hit rate, and a random selection of 76 beat both models on median and
hit rate. The validation advantage did not survive.

**Verdict: do not ship.** The correct action on this evidence is to change
nothing about the scoring model.

### Why the answer was "not yet" rather than "never"

The obstacle looked like sample size and coverage. The stable candidate set is
33 features against 3,655 training rows clustered into 819 tickers and 11 months,
inside a single market regime, with the drift guard removing exactly the features
a longer history would make usable.

Half of that was right and half was wrong. Section 7b is what happened when the
evaluation itself was rebuilt on the same data.

## 7b. The ruler was the problem, 2026-08-30

The null result above rested on a test split of 762 rows across three months,
590 of them in a single month whose mean excess return was +10.7%. Every bar it
applied was mostly a measurement of May 2026. Beside it sat a random baseline
drawn once from a fixed seed, which landed at +15.78% against a distribution
whose median is +8.2%, and that single draw was reported as the floor a
challenger had to clear.

A ruler that cannot separate a model from a lucky draw returns a null result
whatever is put in front of it. `src/research/walkforward.py` replaces it, and
`scripts/hillclimb.py` is the one frozen command that reads it.

### What the new ruler does

**Rolls the origin.** Refit every month on every hold that had already closed,
predict that month, move on. 18 predictable months and 6,690 out-of-sample rows
at 90d, against 762 before.

**Judges inside the month.** 97.5% of the variance in excess-vs-SPY is
within-month, but the 2.5% between months is what a pooled top-k harvests by
accident. Tilt the picks toward months that went up and the mean rises with no
ranking skill at all.

**Charges each pick against its own risk.** Ranking purchases by prior 21-day
realised volatility alone scored +11.9pp with t=3.13 on the first version of the
metric. That is leverage on a rising market. Each pick is now charged against the
mean of its own volatility quintile inside its own month.

**Tests the median.** A fat right tail lifts a mean without any pick being
reliably good, which is the gap the previous candidate died in.

**Prices the search itself.** `permutation_alpha` shuffles labels within month
and re-runs the entire walk-forward fit, so every draw pays the same price in
model search that the real run paid.

`tests/test_walkforward.py` is the sensitivity proof and runs before any number
counts. A perfect ranking scores IC 1.0, a planted signal scores +0.2 under a
month effect twice its size, noise stays inside ±0.1, a ranking that knows only
which months went up scores zero, a book where volatility buys return and
nothing else does is cut by 60%, and a lottery book scores positive on the mean
and negative on the median.

### The shape of the signal

Within-month deciles of how far below its 52-week high a stock sat when the
insider bought:

| decile | discount range | mean | median | hit rate |
|---|---|---|---|---|
| 1 to 9 | 0% to 72% | +0.8% to +2.9% | −5.3% to −0.1% | 41% to 49% |
| 10 | 42% to 99% | **+17.5%** | **+6.6%** | **57.7%** |

Nine flat deciles with negative medians, then a jump. It is a threshold, not a
slope, which is why every rank-transformed linear model scores zero: rank IC on
the discount is −0.02, because there is nothing to order across the bulk.

### The result

Out of sample, walk-forward, top decile of each month, charged against its own
volatility quintile inside its own month, 18 months and 6,690 rows at 90d:

| ranking | alpha | t | median | vs chance |
|---|---|---|---|---|
| distance below 52-week high | **+11.13pp** | **+2.29** | **+7.39pp** | p < 1/5000 |
| ridge on all price context | +9.86pp | +2.33 | +4.54pp | p < 1/300 |
| tier-1 insider features | +1.68pp | +0.52 | −0.15pp | p = 0.10 |
| **the shipped score** | **+0.78pp** | **+0.40** | **+0.47pp** | **p = 0.27** |

The shipped score is a coin flip, now measured over eighteen months rather than
three, under the strongest test available.

The winner is one raw feature with no fitted parameters, so its permutation test
reduces exactly to the random-selection null, which is why the p-value is
sharper than the fitted model's.

### Everything that failed to break it

- **Horizon.** +2.60 at 30d, +6.54 at 60d, +9.86 at 90d, +8.99 at 180d, t from
  1.99 to 2.33, median positive at all four.
- **Selectivity.** t = 2.33, 2.45, 2.51 at the top 10%, 20% and 30% of a month.
- **Subperiod.** +11.25 over the first nine months, +8.48 over the last nine.
- **Survivorship.** All 806 rows whose price series has no exit bar are
  unfinished holds, not delistings, and their rate runs 6.1% to 8.2% across the
  ten discount deciles with no gradient. There is nothing in the evaluable set
  to patch to a total loss.
- **Look-ahead.** Price context is dated strictly before the trade; the minimum
  bar count preceding a purchase is 1.
- **One vote per name.** Collapsing to one row per ticker-month leaves +8.10,
  t=+2.35, median +3.86.
- **Ticker concentration.** Cutting the twenty biggest contributors of 182 names
  takes it to −0.10, which looks fatal until random rankings are cut the same way
  and fall from −0.07 to −2.37. Against that matched null the real curve holds
  the 100th percentile through ten drops and the 97th at twenty.
  `amputation_curve` is that control.

### Everything that failed to improve it

Gating at the training 80th, 90th or 95th percentile lands within noise of the
raw feature, so the gate earns nothing. Ranking by the current score inside the
gate drops it to +7.62 and fails t≥2. Ranking by tier-1 insider features inside
the gate drops it to +6.80. Restricting to small caps drops it to +6.08.
Restricting to anything but large caps drops it to +6.94. Adding trend and
liquidity to a fitted model destroys it, +0.45 with a negative median.

**Insider detail actively degrades the price screen.** Inside the most
discounted third, the number of cluster buyers points the wrong way at −4.53,
t=−1.85, against the CLUSTER_BUY thesis the model is built on.

### The placebo control: the Form 4 is doing the work

Distance below the 52-week high is a known equity effect, so the screen had to
be run on stocks nobody bought. `scripts/insider_control.py` takes every real
purchase and draws placebo observations on a different ticker, the same
transaction date, the same exec date and the same horizon. Calendar, month
structure and holding windows are identical; the only difference is the filing.

| top decile of discount | mean | median | hit rate |
|---|---|---|---|
| insider purchases | +11.13pp | **+7.39pp** | **57.7%** |
| placebo, same dates | +5.55pp | **−1.30pp** | 49.3% |

Half the mean is the discount alone. **All of the median is the insider.** A
deeply discounted stock nobody bought is a lottery ticket, its mean carried by a
fat right tail while the typical one loses money at a 49.3% hit rate. A deeply
discounted stock an insider bought has a positive median and wins 57.7% of the
time.

On the mean alone the honest reading would have been "mostly a value effect".
The median says the opposite. This is what the median statistic was added for.

Two limits. The placebo universe is the 1,371 symbols in the price panel, which
are stocks that had an insider purchase somewhere in the window rather than the
whole market, so the contrast is "no Form 4 on this date" and not "no Form 4
ever". And placebos are drawn at random rather than matched on size or sector,
so the calendar and the discount are controlled but the industry mix is not.

### What this claims

Among insider purchases, the ones in deeply beaten-down stocks outperform their
month-and-risk-matched peers out of sample over eighteen months at p below one
in five thousand, and the same screen without the filing has a negative median.

**The Form 4 is the gate and the discount is the ranker.** Insider attributes do
not rank inside the discounted set; the filing itself is most of the effect.

### The cutoff had to be relative, and measuring after shipping is how that was found

The first cut scored a purchase against a fixed CDF built on the whole research
sample, and classified BUY at 90. That is not the rule the research validated.
The research measured the **top decile of each month**; a fixed threshold on a
distribution the whole market moves together does not select a fixed fraction.

It fired on 2.0% of one month's purchases and 23.7% of another's. In the heavy
months it reached well past the top decile into the nine flat ones, where the
median return is negative. On identical rows and months, top decile, risk
matched:

| rule | mean | t | median | t |
|---|---|---|---|---|
| fixed table, `score >= 90` | +4.19pp | +1.17 | **−2.33pp** | −0.56 |
| trailing 30d, `score >= 90` | +9.92pp | +2.24 | +5.77pp | +1.49 |
| top 10% of the month (ceiling) | +11.10pp | +2.28 | +7.38pp | +1.33 |

More than half the effect was being given away, and the median went negative.
The `discount.py` docstring had called this "alert volume moves with the market"
and treated it as a cost worth paying. That framing was wrong: it is not a volume
problem, it is the selection reaching into the part of the distribution that
carries nothing.

Ranking against the purchases disclosed in the preceding 30 days recovers most of
it. It stays point-in-time because the window holds only filings that already
existed.

**Window length was chosen on a mechanism, not on a maximum.** The rule being
approximated is "the top decile of the current cross-section", so there is a test
that does not look at returns at all: what share of each month clears the cutoff?
It should be a tenth.

| window | refs | mean | t | median | t | share of month |
|---|---|---|---|---|---|---|
| 14 | 210 | +13.60 | +2.39 | +7.95 | +1.47 | 0.0% to 12.6% |
| 21 | 306 | +11.37 | +2.51 | +8.75 | +1.68 | 0.0% to 13.7% |
| **30** | **424** | **+11.05** | **+2.61** | **+7.42** | **+1.87** | **0.8% to 15.6%** |
| 45 | 518 | +8.98 | +2.15 | +2.73 | +0.56 | 2.5% to 18.1% |
| 60 | 687 | +8.02 | +2.05 | +1.09 | +0.24 | 2.0% to 20.0% |
| 90 | 1060 | +9.29 | +2.21 | +1.16 | +0.24 | 2.5% to 20.4% |
| 180 | 2073 | +9.29 | +2.08 | +1.93 | +0.42 | 3.0% to 20.4% |
| 400 | 4323 | +7.07 | +1.79 | +3.30 | +0.75 | 1.5% to 22.2% |
| month | — | +11.10 | +2.28 | +7.38 | +1.33 | 9.1% to 10.1% |

The spread narrows monotonically as the window shortens and the returns follow
it, which is a mechanism and an outcome agreeing rather than a maximum picked out
of a sweep. 30 days rather than 14 or 21 because it rests on 424 reference
purchases against their 210 and 306, and because it is the shortest window that
never leaves a month with no signals at all.

An earlier draft of this section argued for 60 days and dismissed the short end
as a spike. That was measured against a reference inflated 1.23x by counting
broker fills as separate purchases, and without the share-of-month column that
shows the trend is monotone.

### It shipped, 2026-08-30

`src/signals/discount.py` is the model, `src/market/context.py` fetches the input
once at ingest, and four columns on `transactions` store it so the live path and
`backfill_signals.py` read the same number. 14,327 purchase rows backfilled,
92.7% of them rankable from the local panel; all signals rescored.

Verified against the real artifact rather than by inspection. `"SHIPPED scorer"`
is a candidate in the hillclimb registry that calls the production module, and on
the frozen ruler it returns **+11.005, t=+2.25, median +7.413** against the
research feature's +11.132. The 0.13 gap is the integer rounding of the
percentile map.

Then the production backtest, which asks a different question. It measures pooled
excess return against SPY rather than within-month risk-matched selection, and the
old model happened to run the same script one day earlier, so the comparison is
clean: same lookback, same prices, same market period, only the model differs.

| Horizon | old avg | new avg | old median | new median | old hit | new hit | old IR | new IR |
|---|---|---|---|---|---|---|---|---|
| 30d | +4.48% | +4.36% | +1.10% | +1.28% | 55.0% | 53.9% | 0.57 | **0.60** |
| 60d | +7.04% | **+9.05%** | +1.10% | +1.02% | 52.6% | 52.3% | 0.35 | **0.53** |
| 90d | +7.12% | **+13.63%** | −0.90% | **−1.48%** | 48.3% | 48.5% | 0.32 | **0.45** |
| 180d | +16.57% | **+30.45%** | +0.44% | **+9.05%** | 50.6% | **55.3%** | 0.31 | **0.51** |

**Mixed, and the 90d pooled median is negative under both models.** Means and
information ratios improve at every horizon past 30d, 180d improves on every
column, 30d is a wash, and the 90d pooled median got *worse* while the 60d median
went flat.

The pooled median is not the metric the model was selected on, and the reason is
visible in the same output. It collapses 22 months of different market conditions
into one number, so it answers "what did a basket bought across 2024-2026 return"
and moves with which months the model fired in. Split the identical `detail` rows
by month, keeping months with at least 5 signals:

| Horizon | months | shipped beats old | old mean-of-medians | shipped mean-of-medians | old months positive | shipped |
|---|---|---|---|---|---|---|
| 30d | 16 | 11 | −0.46% | **+0.97%** | 8 | **11** |
| 60d | 16 | 7 | +1.16% | +0.57% | 7 | 8 |
| 90d | 15 | 6 | +0.89% | **+3.40%** | 6 | **8** |
| 180d | 12 | 8 | −0.52% | **+10.19%** | 5 | **8** |

At 90d the shipped model wins fewer months but wins them much larger, which is a
fat-tailed improvement rather than a broad one. 60d is the one horizon where it is
honestly no better.

**A superseded run recorded +15.29%/+2.62% at 90d and +34.96%/+13.84% at 180d.**
Those figures are not reproducible and must not be cited. They came from the
fixed-table cutoff replaced later the same day; both runs used
`run_label='scheduled'` on the same `run_date`, so the second delete-then-insert
destroyed the first. The replaced rule scoring *better* on the pooled median while
scoring +4.19/−2.33 on the pre-registered within-month ruler — against the shipped
rule's +9.92/+5.77 — is itself the argument for not selecting on the pooled number.
A research run should have carried `--label`; that it did not is the mistake this
paragraph exists to record.

Thresholds moved with the scale: BUY 60 to 90, WATCH 45 to 70, cluster average 22
to 80 and max 30 to 85. CLUSTER_BUY dropped from 136 signals to 60, which is the
intended effect of requiring the group to have been buying weakness.

### What shipping it required

`tx_pct_below_52wk_high` is computed from the price panel at the transaction
date. The live path has a Yahoo quote and the backfill path does not, which is
the exact reason section 2 gives for deleting the old 52-week factors: the same
purchase scored up to 12 points apart depending on which entry point saw it, and
they compared against *today's* low rather than the low as of the trade.

So the rule in CLAUDE.md stands, and the way to satisfy it is to move the fetch
rather than to drop the factor. Phase 1B, the point-in-time price context stored
on the transaction row at ingest, was deliberately skipped in the first round
because no model read it. A model reads it now, so it earned its place, and it
went in first: store the context at ingest, backfill it for stored transactions,
then add the factor, then `backfill_signals.py --days 730 --force`, then
`run_backtest.py`. `score_transaction` remains a pure function of stored data.

The open question this leaves is the one the placebo control could not answer.
The control universe is the 1,371 symbols in the price panel, which are stocks
that had an insider purchase somewhere in the window, and the placebos are drawn
at random rather than matched on size or sector. A control matched on both would
say how much of the +5.55pp non-insider mean is industry mix.

### What did ship from the first round

Nothing that changes a score. What landed is the apparatus and five corrections:

- returns measured on dividend-adjusted closes (Phase 1)
- `run_label` on `backtest_runs`, so a re-run cannot destroy its own baseline
- a local price panel proven equivalent to the network path to 0.003pp
- the negative class scored, with `verify_scoring_parity.py` holding the research
  path and the pipeline to 99% agreement
- `_clean_ticker` refusing tickers no price API can resolve

## 8. Risks

**Overfitting is the primary risk and it is already partly realised.** Five rounds of tuning
have been spent on this data. Even a correct protocol applied now inherits contaminated
priors. Mitigation: prefer the simplest model that works, pre-register the test evaluation,
and treat a null result as publishable.

**One regime.** 730 days. Nothing here can distinguish a factor that works from one that
worked since 2024. Mitigation: report performance by calendar half-year and refuse to ship a
factor whose sign flips across sub-periods.

**Thin cells persist.** Adding features to a 9,477-row dataset with heavy ticker clustering
does not make chairman purchases numerous. Enforce the n ≥ 100 floor honestly.

**Yahoo Finance is unofficial.** The whole label depends on it. The price panel makes this
worse in one way — a single bad fetch poisons every experiment — and better in another, since
a cached panel is auditable and reproducible where per-run fetches are not. Snapshot the
panel with a checksum and never silently refetch.

**Neon 0.5GB.** At 105MB now. `scored_purchases` and the price-context columns add a few MB.
Keeping the price panel local is what makes this safe; do not move it into Neon without
re-checking headroom.

**Survivorship in the price panel.** Fetching by current ticker misses renames and
acquisitions. The existing −50% delisting convention is a blunt instrument that the panel will
make easier to examine but does not by itself fix.

## 9. What this plan deliberately does not do

- It does not change any weight, threshold, or classification rule before Phase 7.
- It does not touch ingest, the parser, cluster detection, or the alerting path.
- It does not add a factor the backfill cannot compute. The price-context columns exist
  specifically to preserve that invariant.
- It does not replace `signals`. The dashboard and Telegram contract is unchanged.

## 10. References

- Lakonishok & Lee (2001), *Are Insider Trades Informative?*
- Cohen, Malloy & Pomorski (2012), [*Decoding Inside Information*](https://onlinelibrary.wiley.com/doi/abs/10.1111/j.1540-6261.2012.01740.x), Journal of Finance 67(3)
- Jeng, Metrick & Zeckhauser (2003), *Estimating the Returns to Insider Trading*
- Cline, Gokkaya & Liu (2017), [*The Persistence of Opportunistic Insider Trading*](https://onlinelibrary.wiley.com/doi/10.1111/fima.12177), Financial Management
- [*Insider Purchase Signals in Microcap Equities: Gradient Boosting Detection of Abnormal Returns*](https://arxiv.org/html/2602.06198) (arXiv 2602.06198) — preprint, not peer reviewed; treat the feature-importance figures as indicative
- [FINRA Equity Short Interest](https://www.finra.org/finra-data/browse-catalog/equity-short-interest)
